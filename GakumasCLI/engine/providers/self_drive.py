# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-driven Produce input provider."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from GakumasCLI.engine.produce_run import ProduceIdentity, ProduceRun, ProduceStep
from GakumasCLI.engine.produce_steps import load_produce_setting, refresh_stamina_delta
from GakumasCLI.engine.server_draw import ServerDraw

from .corpus import R21_BLOCK_CUSTOMIZE, R21_MEMORY_ABILITY, R21_PRIDE_CUSTOMIZE, default_r21_spec


def _decoded_rows(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("decode_status", "decoded")).lower() not in {"decoded", "decode"}:
                continue
            decoded = row.get("decoded", row)
            if isinstance(decoded, Mapping):
                yield decoded


def _table_ids(master_dir: str | Path, table_name: str) -> set[str]:
    return {str(row.get("id")) for row in _decoded_rows(Path(master_dir) / f"{table_name}.jsonl") if row.get("id")}


def _is_audition(step: Mapping[str, Any]) -> bool:
    text = f"{step.get('step_type', '')} {step.get('step_id', '')}".upper()
    return "AUDITION" in text


def _is_final(step: Mapping[str, Any]) -> bool:
    text = f"{step.get('step_type', '')} {step.get('step_id', '')}".upper()
    return "FINAL" in text


def _first_present(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first explicitly supplied value, retaining numeric zeroes."""

    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


class SelfDriveProvider:
    """Create a ProduceRun from a Python spec and an optional run seed."""

    def __init__(self, spec: Mapping[str, Any] | None = None, *, master_dir: str | Path | None = None):
        self.spec = copy.deepcopy(dict(spec) if spec is not None else default_r21_spec())
        self.master_dir = Path(master_dir) if master_dir is not None else Path(__file__).resolve().parents[2] / "ro" / "master" / "tables"
        self._run: ProduceRun | None = None
        self.server_draw: ServerDraw | None = None

    @property
    def run(self) -> ProduceRun:
        if self._run is None:
            if self.master_dir is None:
                raise RuntimeError("SelfDriveProvider.build(master_dir) is required before run is available")
            self.build(self.master_dir)
        assert self._run is not None
        return self._run

    @property
    def produced_run(self) -> ProduceRun:
        """Compatibility spelling for callers that avoid the ``run`` property."""

        return self.run

    def _steps_spec(self) -> list[dict]:
        raw = self.spec.get("steps")
        if raw is None:
            raw = self.spec.get("step_sequence")
        if isinstance(raw, Mapping):
            raw = list(raw.values())
        if not isinstance(raw, (list, tuple)) or not raw:
            raise ValueError("SelfDriveProvider requires a non-empty steps/step_sequence")
        return [dict(row) for row in raw]

    @staticmethod
    def _normalize_supports(spec: Mapping[str, Any]) -> list[dict]:
        supports = _first_present(spec, "supports", "support_cards", default=())
        result = []
        for number, row in enumerate(supports, 1):
            if not isinstance(row, Mapping):
                continue
            item = dict(row)
            item.setdefault("number", number)
            item["support_card_id"] = str(item.get("support_card_id") or item.get("id") or "")
            item["level"] = int(item.get("level") or 0)
            item["produce_skill_ids"] = list(item.get("produce_skill_ids") or ())
            result.append(item)
        return result

    @staticmethod
    def _normalize_memories(spec: Mapping[str, Any]) -> list[dict]:
        memories = _first_present(spec, "memories", "memory_list", default=())
        result = []
        for number, row in enumerate(memories, 1):
            if not isinstance(row, Mapping):
                continue
            item = copy.deepcopy(dict(row))
            item.setdefault("number", number)
            item.setdefault("is_rental", False)
            item.setdefault("phase_type", item.get("produce_card_phase_type") or "END_AUDITION_MID")
            item.setdefault("produce_card_phase_type", item["phase_type"])
            card = item.get("produce_card") or {}
            item["produce_card"] = dict(card)
            item["produce_card"].setdefault("upgrade_count", 0)
            item["abilities"] = list(item.get("abilities") or ())
            item["customizes"] = copy.deepcopy(list(item.get("customizes") or ()))
            item_ids = _first_present(item, "item_ids", "exam_battle_produce_item_ids", default=())
            item["item_ids"] = list(item_ids)
            result.append(item)
        return result

    @staticmethod
    def _normalize_deck(spec: Mapping[str, Any]) -> list[dict]:
        cards = _first_present(spec, "deck_cards", "deck", default=())
        result = []
        for number, row in enumerate(cards, 1):
            item = dict(row)
            item.setdefault("number", number)
            item["produce_card_id"] = str(item.get("produce_card_id") or item.get("id") or "")
            item["base_upgrade_count"] = int(_first_present(item, "base_upgrade_count", "upgrade_count", default=0))
            item.setdefault("origin_type", 0)
            item.setdefault("is_initial", False)
            item["customizes"] = copy.deepcopy(list(item.get("customizes") or ()))
            result.append(item)
        return result

    @staticmethod
    def _step_id(row: Mapping[str, Any], index: int, produce_id: str) -> str:
        if row.get("step_id"):
            return str(row["step_id"])
        if str(row.get("step_type", "")).upper().startswith("AUDITION"):
            return "AuditionFinal" if "FINAL" in str(row.get("step_type", "")).upper() else "AuditionMid1"
        phase = "after_mid" if index > 4 else "before_mid"
        kind = str(row.get("lesson_kind") or row.get("lesson_type") or row.get("step_type") or "normal").lower()
        if "sp" in kind:
            kind = "sp"
        elif "hard" in kind:
            kind = "hard"
        else:
            kind = "normal"
        number = int(_first_present(row, "step_number", default=index))
        return f"p_step_lesson-{produce_id}-exam_full_power_01-{kind}-{number:02d}"

    @staticmethod
    def _human_type(row: Mapping[str, Any]) -> str:
        value = str(row.get("segment_type") or row.get("step_type") or "")
        if value.startswith("PRODUCE_STEP_TYPE_"):
            value = value.removeprefix("PRODUCE_STEP_TYPE_").replace("_", " ")
            if value.startswith("LESSON "):
                parts = value.split()
                if len(parts) >= 3:
                    return f"Lesson {parts[1].title()} {parts[2]}"
            if value.startswith("AUDITION "):
                return "Audition " + value.removeprefix("AUDITION ").title()
        return value

    def _lesson_gimmick(self, identity: ProduceIdentity, row: Mapping[str, Any], index: int,
                        after_mid: bool, master_dir: Path, known_ids: set[str]) -> tuple[str | None, bool]:
        if row.get("gimmick_group_id") is not None or row.get("gimmick") is not None:
            return row.get("gimmick_group_id", row.get("gimmick")), False
        text = f"{row.get('step_type', '')} {row.get('segment_type', '')}".lower()
        if "sp" in text:
            kind = "sp"
        elif "hard" in text:
            kind = "hard"
        else:
            kind = "normal"
        phase = "after_mid" if after_mid else "before_mid"
        family = {47: "exam_full_power_01"}.get(identity.exam_effect_type, f"exam_effect_{identity.exam_effect_type}")
        candidate = f"p_exam_gimmick-{identity.produce_id}-{family}-lesson_{kind}-{phase}"
        if candidate in known_ids:
            return candidate, False
        return None, True

    def _provenance(self, run: ProduceRun, steps: list[dict], rolled: set[int]) -> None:
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
                locator = (
                    "wire_selected_decoded.json:event_sequence=7/updated_data.user_produce_progress_support_card_list"
                    if self.spec.get("_r21_observed") else "SelfDriveProvider.spec:supports"
                )
                run.provenance[path] = {"source": "b", "locator": locator}
            elif path.startswith("memories["):
                extra = " unverified: provider supplied memory ability/customize identity" if ("abilities" in path or "customizes" in path) else ""
                locator = (
                    "wire_selected_decoded.json:event_sequence=7/updated_data.user_produce_progress_memory_list"
                    if self.spec.get("_r21_observed") else "SelfDriveProvider.spec:memories"
                )
                run.provenance[path] = {"source": "b", "locator": locator + extra}
            elif path.startswith("item_ids["):
                locator = (
                    "wire_selected_decoded.json:event_sequence=271/produce_history plus inferred rental-memory item inclusion"
                    if self.spec.get("_r21_observed") else "SelfDriveProvider.spec:item_ids"
                )
                run.provenance[path] = {"source": "b", "locator": locator}
            elif path.startswith("deck_cards["):
                locator = "segment_deck.json:segments[0]/cards" if self.spec.get("_r21_observed") else "SelfDriveProvider.spec:deck_cards"
                run.provenance[path] = {"source": "b", "locator": locator}
            elif path == "deck_snapshots":
                run.provenance[path] = {"source": "c", "locator": "approximation:self-drive has no corpus deck snapshots"}
            elif path.startswith("steps["):
                index = int(path.split("[", 1)[1].split("]", 1)[0])
                if "exam_seed_value" in path and index + 1 in rolled:
                    entry = {"source": "c", "locator": f"ServerDraw:exam_seed key={index + 1} rolled per-kind seed"}
                elif path.endswith(".stamina"):
                    entry = {"source": "c", "locator": "approximation:carry_over; before audition refresh_stamina_delta"}
                elif "gimmick_group_id" in path and steps[index].get("_gimmick_unverified"):
                    entry = {"source": "c", "locator": "unverified: derived gimmick id not present in master"}
                else:
                    field_name = path.rsplit(".", 1)[-1]
                    if self.spec.get("_r21_observed"):
                        if field_name == "support_permil":
                            locator = f"segment_initial_state.json:records[{index}]/support_rows"
                        else:
                            locator = f"wire_step_starts.jsonl:step_index={index + 1}/user_produce_progress.{field_name}"
                    else:
                        locator = "SelfDriveProvider.spec:steps"
                    entry = {"source": "b", "locator": locator}
                run.provenance[path] = entry
            elif path == "stamina":
                run.provenance[path] = {"source": "c", "locator": "approximation:carry_over; current segment owner"}
            elif path in {"max_stamina", "vocal", "dance", "visual"}:
                locator = (
                    "wire_step_starts.jsonl:step_index=1/user_produce_progress.max_stamina"
                    if self.spec.get("_r21_observed") and path == "max_stamina"
                    else "SelfDriveProvider.spec:run state"
                )
                run.provenance[path] = {"source": "b", "locator": locator}
            elif path in {"supports", "memories", "item_ids", "deck_cards"}:
                run.provenance[path] = {"source": "b", "locator": f"SelfDriveProvider.spec:{path}"}
            else:
                raise AssertionError(f"unhandled self-drive provenance path: {path}")

    def build(self, master_dir: str | Path | None = None, *, idol_card_id: str | None = None,
              produce_id: str | None = None) -> ProduceRun:
        selected_master = Path(master_dir or self.master_dir or (Path(__file__).resolve().parents[2] / "ro" / "master" / "tables"))
        selected_idol = str(idol_card_id or self.spec.get("idol_card_id") or "i_card-hmsz-3-000")
        selected_produce = str(produce_id or self.spec.get("produce_id") or "produce-002")
        identity = ProduceIdentity.from_master(selected_master, selected_idol, selected_produce)
        supports = self._normalize_supports(self.spec)
        memories = self._normalize_memories(self.spec)
        deck = self._normalize_deck(self.spec)
        rows = self._steps_spec()
        max_stamina = int(_first_present(self.spec, "max_stamina", default=identity.base_stamina))
        run_seed = self.spec.get("run_seed")
        run_seed = None if run_seed is None else int(run_seed)
        observed: dict[tuple[str, Any], int] = {}
        explicit_observed = self.spec.get("observed_exam_seeds") or {}
        for index, row in enumerate(rows, 1):
            if isinstance(explicit_observed, Mapping) and index in explicit_observed:
                observed[("exam_seed", index)] = int(explicit_observed[index])
            elif isinstance(explicit_observed, Mapping) and str(index) in explicit_observed:
                observed[("exam_seed", index)] = int(explicit_observed[str(index)])
            elif row.get("observed_exam_seed") is True or row.get("observed") is True:
                value = row.get("exam_seed_value", row.get("seed"))
                if value is not None:
                    observed[("exam_seed", index)] = int(value)
            elif run_seed is None and row.get("seed") is not None:
                observed[("exam_seed", index)] = int(row["seed"])
        self.server_draw = ServerDraw(0 if run_seed is None else run_seed, observed)
        known_gimmicks = _table_ids(selected_master, "ProduceExamGimmickEffectGroup")
        steps: list[ProduceStep] = []
        rolled: set[int] = set()
        current_stamina = max_stamina
        mid_seen = False
        for index, row in enumerate(rows, 1):
            step_id = self._step_id(row, index, selected_produce)
            step_type = self._human_type(row)
            if not step_type:
                step_type = str(row.get("step_type") or "")
            seed = self.server_draw.resolve(
                "exam_seed", index,
                roll=lambda rng: rng.range(1, 2**31 - 1),
            )
            if run_seed is not None and ("exam_seed", index) not in observed:
                rolled.add(index)
            if _is_audition(row) and index > 1:
                setting = load_produce_setting(selected_master, identity.produce_setting_id)
                current_stamina += refresh_stamina_delta(
                    max_stamina, current_stamina,
                    setting["beforeAuditionRefreshStaminaRecoveryPermil"],
                )
            gimmick = None
            gimmick_unverified = False
            if not _is_audition(row):
                gimmick, gimmick_unverified = self._lesson_gimmick(identity, row, index, mid_seen, selected_master, known_gimmicks)
            support_permil = {str(key): int(value) for key, value in (row.get("support_permil") or {}).items()}
            step = ProduceStep(
                index=index,
                step_type=step_type,
                step_id=step_id,
                exam_seed_value=int(seed),
                gimmick_group_id=gimmick,
                stamina=int(current_stamina),
                max_stamina=max_stamina,
                vocal=int(row.get("vocal") or 0),
                dance=int(row.get("dance") or 0),
                visual=int(row.get("visual") or 0),
                support_permil=support_permil,
                audition_bonus_permil=int(_first_present(row, "audition_bonus_permil", "bonus", default=0)),
                vote_bonus_permil=int(_first_present(row, "vote_bonus_permil", "vote_bonus", default=0)),
            )
            row["_gimmick_unverified"] = gimmick_unverified
            steps.append(step)
            if _is_audition(row) and not _is_final(row):
                mid_seen = True

        run = ProduceRun(
            identity=identity,
            supports=supports,
            memories=memories,
            item_ids=list(_first_present(self.spec, "item_ids", default=())),
            deck_cards=deck,
            stamina=max_stamina,
            max_stamina=max_stamina,
            vocal=int(_first_present(rows[0], "vocal", default=identity.base_vocal)),
            dance=int(_first_present(rows[0], "dance", default=identity.base_dance)),
            visual=int(_first_present(rows[0], "visual", default=identity.base_visual)),
            steps=steps,
        )
        run.server_draw = self.server_draw
        run.run_seed = run_seed
        run.provenance["run_seed"] = {
            "source": "b" if run_seed is not None else "c",
            "locator": "SelfDriveProvider.spec:run_seed" if run_seed is not None else "ServerDraw:default run_seed=0",
        }
        run.dearness_level = self.spec.get("dearness_level")
        run._provider = self
        run.provenance["dearness_level"] = {
            "source": "b",
            "locator": "wire_step_starts.jsonl:step_index=1/user_produce_progress.start_dearness_level"
            if self.spec.get("_r21_observed")
            else "SelfDriveProvider.spec:dearness_level",
        }
        self._provenance(run, rows, rolled)
        run.assert_provenance_complete()
        self._run = run
        self.master_dir = selected_master
        return run

    def prepare_step(self, index: int, run: ProduceRun | None = None) -> ProduceStep:
        """Prepare the requested segment's stamina and return its step block."""

        run = run or self.run
        step = run.step(index)
        position = run._step_position(index)
        if position == 0:
            run.stamina = run.max_stamina
        elif _is_audition({"step_type": step.step_type, "step_id": step.step_id}):
            setting = load_produce_setting(run._provider.master_dir, run.identity.produce_setting_id)
            run.stamina += refresh_stamina_delta(
                run.max_stamina,
                run.stamina,
                setting["beforeAuditionRefreshStaminaRecoveryPermil"],
            )
        step.stamina = int(run.stamina)
        run.provenance["stamina"] = {"source": "c", "locator": f"approximation:carry_over; prepared step={step.index}"}
        step_path = f"steps[{position}].stamina"
        run.provenance[step_path] = {"source": "c", "locator": f"approximation:carry_over; prepared step={step.index}"}
        return step


__all__ = [
    "SelfDriveProvider",
    "default_r21_spec",
    "R21_MEMORY_ABILITY",
    "R21_PRIDE_CUSTOMIZE",
    "R21_BLOCK_CUSTOMIZE",
]
