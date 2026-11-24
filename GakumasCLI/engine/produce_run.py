# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-segment produce input objects."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .audition import AuditionCatalog
from .card_data import effective_audition_parameter_permil
from .lessons import LessonCatalog
from .server_draw import ServerDraw


_COMMON_PLAN_TYPE = 1
_AUDITION_MID1 = "AUDITION_MID1"
_AUDITION_FINAL = "AUDITION_FINAL"


def _decoded_rows(path: Path) -> Iterable[dict]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("decode_status", "decoded") != "decoded":
                continue
            decoded = row.get("decoded", row)
            if isinstance(decoded, dict):
                yield decoded


def _table_dir(master_dir: str | Path) -> Path:
    root = Path(master_dir)
    if (root / "IdolCard.jsonl").exists():
        return root
    tables = root / "tables"
    if (tables / "IdolCard.jsonl").exists():
        return tables
    return root


def _enum_number(value: Any, default: int | None = None) -> int | None:
    if isinstance(value, Mapping):
        value = value.get("number", default)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _enum_name(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("name") or "")
    return str(value or "")


def _mapping_value(owner: Any, name: str) -> Any:
    if isinstance(owner, Mapping):
        return owner.get(name)
    return getattr(owner, name, None)


def _lookup(table: Any, key: Any) -> Any:
    if table is None:
        return None
    if isinstance(table, Mapping):
        value = table.get(key)
        if value is not None:
            return value
        if isinstance(key, tuple) and key:
            value = table.get(key[0])
            if value is not None:
                return value
        return None
    for row in table:
        if not isinstance(row, Mapping):
            continue
        if row.get("id") == key:
            return row
    return None


def _master_dir(master: Any) -> Path | None:
    if isinstance(master, (str, Path)):
        return _table_dir(master)
    for name in ("master_dir", "tables_dir", "directory"):
        value = _mapping_value(master, name)
        if value:
            return _table_dir(value)
    return None


def _load_table(master: Any, attr: str, table_name: str) -> dict:
    table = _mapping_value(master, attr)
    if table is not None:
        return table
    root = _master_dir(master)
    if root is None:
        return {}
    path = root / f"{table_name}.jsonl"
    if not path.exists():
        return {}
    return {row["id"]: row for row in _decoded_rows(path) if row.get("id") is not None}


MEMORY_ABILITY_TABLES = {"memory_abilities": "MemoryAbility", "produce_skills": "ProduceSkill",
                         "produce_effects": "ProduceEffect", "produce_triggers": "ProduceTrigger"}


def _skill_level(level):
    try:
        return int(level) if level is not None else None
    except (TypeError, ValueError):
        return level


def load_memory_ability_tables(master_dir) -> dict:
    """The four produce-scope tables behind a memory's ability, keyed as the join reads them: MemoryAbility,
    ProduceEffect and ProduceTrigger by id, ProduceSkill by (id, level) -- MemoryAbility.skillId and.level are that
    key.
    """
    root = Path(master_dir)
    out = {}
    for attr, table in MEMORY_ABILITY_TABLES.items():
        path = root / f"{table}.jsonl"
        rows = list(_decoded_rows(path)) if path.exists() else []
        if attr == "produce_skills":
            out[attr] = {(row["id"], _skill_level(row.get("level"))): row for row in rows}
        else:
            out[attr] = {row["id"]: row for row in rows}
    return out


def join_memory_ability(tables: Mapping[str, Any], ability_id) -> list[dict]:
    """The fully joined activation triples of one memory ability, from ``load_memory_ability_tables``' tables."""
    ability = tables["memory_abilities"].get(ability_id)
    if ability is None:
        return []
    skill = tables["produce_skills"].get((ability.get("skillId"), _skill_level(ability.get("level"))))
    if skill is None:
        return []
    activations = []
    for index in range(1, 4):
        effect_id = skill.get(f"produceEffectId{index}")
        trigger_id = skill.get(f"produceTriggerId{index}")
        rate = skill.get(f"activationRatePermil{index}")
        if effect_id is None and trigger_id is None:
            continue
        if (effect_id is None or trigger_id is None
                or effect_id not in tables["produce_effects"] or trigger_id not in tables["produce_triggers"]):
            continue
        activations.append({"effect_id": effect_id, "trigger_id": trigger_id, "activation_rate_permil": rate})
    return activations


@dataclass
class ProduceIdentity:
    produce_id: str
    idol_card_id: str
    plan_type: int
    exam_effect_type: int
    difficulty_id: str
    initial_deck_id: str
    idol_produce_card_id: str
    base_vocal: int
    base_dance: int
    base_visual: int
    base_stamina: int
    before_item_id: str
    after_item_id: str
    exam_setting_id: str
    produce_setting_id: str
    steps: int

    @classmethod
    def from_master(cls, master_dir: str | Path, idol_card_id: str, produce_id: str) -> "ProduceIdentity":
        """Join one IdolCard row with its Produce row from decoded JSONL."""

        root = _table_dir(master_dir)
        idol_rows = {row.get("id"): row for row in _decoded_rows(root / "IdolCard.jsonl")}
        produce_rows = {row.get("id"): row for row in _decoded_rows(root / "Produce.jsonl")}
        try:
            idol = idol_rows[idol_card_id]
        except KeyError as exc:
            raise KeyError(f"IdolCard {idol_card_id!r}") from exc
        try:
            produce = produce_rows[produce_id]
        except KeyError as exc:
            raise KeyError(f"Produce {produce_id!r}") from exc
        return cls(
            produce_id=produce_id,
            idol_card_id=idol_card_id,
            plan_type=int(_enum_number(idol.get("planType"), 0)),
            exam_effect_type=int(_enum_number(idol.get("examEffectType"), 0)),
            difficulty_id=str(idol.get("produceStepAuditionDifficultyId") or ""),
            initial_deck_id=str(idol.get("examInitialDeckId") or ""),
            idol_produce_card_id=str(idol.get("produceCardId") or ""),
            base_vocal=int(idol.get("produceVocal") or 0),
            base_dance=int(idol.get("produceDance") or 0),
            base_visual=int(idol.get("produceVisual") or 0),
            base_stamina=int(idol.get("produceStamina") or 0),
            before_item_id=str(idol.get("beforeProduceItemId") or ""),
            after_item_id=str(idol.get("afterProduceItemId") or ""),
            exam_setting_id=str(produce.get("examSettingId") or ""),
            produce_setting_id=str(produce.get("produceSettingId") or ""),
            steps=int(produce.get("steps") or 0),
        )


@dataclass
class ProduceStep:
    """One segment's server-supplied block."""

    index: int
    step_type: str
    step_id: str
    exam_seed_value: int
    gimmick_group_id: str | None
    stamina: int
    max_stamina: int
    vocal: int
    dance: int
    visual: int
    support_permil: dict
    audition_bonus_permil: int
    vote_bonus_permil: int = 0


@dataclass
class ProduceRun:
    """State shared by all Lesson/Audition segments of one produce."""

    identity: ProduceIdentity
    supports: list = field(default_factory=list)
    memories: list = field(default_factory=list)
    item_ids: list = field(default_factory=list)
    deck_cards: list = field(default_factory=list)
    stamina: int = 0
    max_stamina: int = 0
    vocal: int = 0
    dance: int = 0
    visual: int = 0
    steps: list[ProduceStep] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    deck_snapshots: dict = field(default_factory=dict)

    def _step_position(self, step_index: int) -> int:
        if not self.steps:
            raise IndexError("ProduceRun has no steps")
        try:
            requested = int(step_index)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"invalid step index: {step_index!r}") from exc
        for position, step in enumerate(self.steps):
            if int(step.index) == requested:
                return position
        if 0 <= requested < len(self.steps):
            return requested
        if 1 <= requested <= len(self.steps):
            return requested - 1
        raise IndexError(requested)

    def step(self, step_index: int) -> ProduceStep:
        return self.steps[self._step_position(step_index)]

    @staticmethod
    def _step_kind(step: ProduceStep) -> str:
        text = f"{step.step_type} {step.step_id}".upper().replace("-", "_").replace(" ", "_")
        if "AUDITION" in text:
            if "FINAL" in text:
                return _AUDITION_FINAL
            if "MID1" in text or "MID_1" in text or "AUDITIONMID1" in text:
                return _AUDITION_MID1
            if "MID2" in text or "MID_2" in text or "AUDITIONMID2" in text:
                return "AUDITION_MID2"
            return "AUDITION"
        return "LESSON"

    @staticmethod
    def _lesson_parameter(step: ProduceStep) -> int | None:
        text = f"{step.step_type} {step.step_id}".upper()
        if "VOCAL" in text:
            return 1
        if "DANCE" in text:
            return 2
        if "VISUAL" in text:
            return 3
        return None

    @staticmethod
    def _catalog(master: Any, attr: str, loader: Any):
        catalog = _mapping_value(master, attr)
        if catalog is not None:
            return catalog
        root = _master_dir(master)
        if root is None:
            raise TypeError("master must provide a table directory or a catalog")
        return loader.load(root)

    def segment_config(self, master: Any, step_index: int):
        """Build the existing ``scenario.exam_session.SegmentConfig``."""

        from GakumasCLI.scenario.exam_session import SegmentConfig

        step = self.step(step_index)
        kind = self._step_kind(step)
        common = {
            "segment_id": step.step_id,
            "segment_type": step.step_type,
            "seed": int(step.exam_seed_value),
            "final_audition": kind == _AUDITION_FINAL,
        }
        if kind == "LESSON":
            lessons = self._catalog(master, "lesson_catalog", LessonCatalog)
            limit_turn, score_cap, _threshold = lessons.setup(step.step_id)
            return SegmentConfig(
                **common,
                gimmick_group_id=step.gimmick_group_id,
                limit_turn=int(limit_turn),
                score_cap=int(score_cap),
                parameter_type=self._lesson_parameter(step),
                audition_permils=None,
                audition_parameter_values=None,
            )

        auditions = self._catalog(master, "audition_catalog", AuditionCatalog)
        audition_kind = kind if kind in (_AUDITION_MID1, _AUDITION_FINAL) else kind
        difficulty = auditions.difficulty(
            self.identity.difficulty_id,
            audition_kind,
            self.identity.produce_id,
        )
        battle_id = difficulty["produceExamBattleConfigId"]
        battle = auditions.battle_configs[battle_id]
        values = {1: int(step.vocal), 2: int(step.dance), 3: int(step.visual)}
        base_permils = auditions.base_permils_for_difficulty(
            self.identity.difficulty_id,
            audition_kind,
            values,
            self.identity.produce_id,
        )
        permils = {
            parameter_type: effective_audition_parameter_permil(
                value,
                int(step.audition_bonus_permil),
                int(step.vote_bonus_permil),
            )
            for parameter_type, value in base_permils.items()
        }
        gimmick = step.gimmick_group_id or difficulty.get("produceExamGimmickEffectGroupId")
        return SegmentConfig(
            **common,
            gimmick_group_id=gimmick,
            limit_turn=int(battle["turn"]),
            score_cap=int(difficulty.get("forceEndScore") or difficulty.get("baseScore") or 0),
            parameter_type=None,
            audition_permils=permils,
            audition_parameter_values=values,
        )

    def item_enchant_ids(self, master: Any) -> list[str]:
        """Resolve equipped ProduceItems through ProduceItemEffect rows."""

        items = _load_table(master, "items", "ProduceItem")
        item_effects = _load_table(master, "item_effects", "ProduceItemEffect")
        enchants: list[str] = []
        for item_id in self.item_ids:
            item = _lookup(items, item_id)
            if item is None:
                raise KeyError(f"ProduceItem {item_id!r}")
            effect_ids = list(item.get("produceItemEffectIds") or ())
            if not effect_ids:
                effect_ids = [skill.get("produceItemEffectId") for skill in item.get("skills") or () if skill.get("produceItemEffectId")]
            for effect_id in effect_ids:
                effect = _lookup(item_effects, effect_id)
                if effect is None:
                    raise KeyError(f"ProduceItemEffect {effect_id!r}")
                enchant_id = effect.get("produceExamStatusEnchantId") or effect.get("produce_exam_status_enchant_id")
                if not enchant_id:
                    raise KeyError(f"ProduceItemEffect {effect_id!r} has no enchant id")
                enchants.append(str(enchant_id))
        return enchants

    @staticmethod
    def _memory_payload(memory: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = memory.get("memory") if isinstance(memory.get("memory"), Mapping) else memory
        return payload

    def memory_cards_for(self, step_index: int) -> list[dict]:
        """Return memory-card instances eligible in the requested segment."""

        position = self._step_position(step_index)
        has_finished_mid = any(
            self._step_kind(candidate) == _AUDITION_MID1
            for candidate in self.steps[:position]
        )
        first_segment = position == 0
        cards: list[dict] = []
        for memory in self.memories:
            if not isinstance(memory, Mapping):
                continue
            payload = self._memory_payload(memory)
            phase = str(
                payload.get("produce_card_phase_type")
                or payload.get("phase_type")
                or memory.get("phase_type")
                or ""
            ).upper()
            is_start = "PRODUCE_START" in phase
            is_end_mid = "END_AUDITION_MID" in phase
            if not ((is_start and first_segment) or (is_end_mid and has_finished_mid)):
                continue
            produce_card = payload.get("produce_card") or memory.get("produce_card") or {}
            if not isinstance(produce_card, Mapping):
                produce_card = {}
            card_id = produce_card.get("id") or produce_card.get("produce_card_id") or payload.get("produce_card_id")
            if not card_id:
                continue
            customizes = payload.get("customizes")
            if customizes is None:
                customizes = memory.get("customizes")
            if customizes is None:
                customizes = produce_card.get("customizes")
            cards.append(
                {
                    "produce_card_id": str(card_id),
                    "base_upgrade_count": int(produce_card.get("upgrade_count") or 0),
                    "customizes": copy.deepcopy(list(customizes or ())),
                    "origin_type": 4,
                }
            )
        return cards

    def deck_cards_for(self, step_index: int) -> list[dict]:
        """Return base cards plus cards joined from memories for a segment."""
        snapshots = self.deck_snapshots or getattr(self, "_deck_snapshots", None)
        if isinstance(snapshots, Mapping):
            requested = int(step_index)
            snapshot = snapshots.get(requested)
            if snapshot is None and 0 <= requested < len(self.steps):
                snapshot = snapshots.get(requested + 1)
            if snapshot is not None:
                return copy.deepcopy(list(snapshot))
        return copy.deepcopy(list(self.deck_cards or ())) + self.memory_cards_for(step_index)

    # -- memory ability join ------------------------------------------
    def _ability_skill(self, master: Any, ability_id: str) -> Mapping[str, Any] | None:
        abilities = _load_table(master, "memory_abilities", "MemoryAbility")
        skills = _load_table(master, "produce_skills", "ProduceSkill")
        ability = _lookup(abilities, ability_id)
        if ability is None:
            return None
        return _lookup(skills, (ability.get("skillId"), ability.get("level"))) or _lookup(skills, ability.get("skillId"))

    def _ability_joins(self, master: Any, ability_id: str) -> tuple[list[dict], Mapping[str, Any] | None]:
        """The ability's (effect, trigger, rate) triples and its ProduceSkill row."""
        joiner = _mapping_value(master, "memory_ability_activations")
        if callable(joiner):
            return list(joiner(ability_id) or ()), self._ability_skill(master, ability_id)
        if isinstance(joiner, Mapping):
            return list(joiner.get(ability_id) or ()), self._ability_skill(master, ability_id)

        abilities = _load_table(master, "memory_abilities", "MemoryAbility")
        skills = _load_table(master, "produce_skills", "ProduceSkill")
        effects = _load_table(master, "produce_effects", "ProduceEffect")
        triggers = _load_table(master, "produce_triggers", "ProduceTrigger")
        ability = _lookup(abilities, ability_id)
        if ability is None:
            return [], None
        skill_id = ability.get("skillId")
        level = ability.get("level")
        skill = _lookup(skills, (skill_id, level)) or _lookup(skills, skill_id)
        if skill is None:
            return [], None
        out = []
        for index in range(1, 4):
            effect_id = skill.get(f"produceEffectId{index}")
            trigger_id = skill.get(f"produceTriggerId{index}")
            if not effect_id and not trigger_id:
                continue
            if effect_id and trigger_id and (_lookup(effects, effect_id) is not None or not effects) and (_lookup(triggers, trigger_id) is not None or not triggers):
                out.append({
                    "effect_id": effect_id,
                    "trigger_id": trigger_id,
                    "activation_rate_permil": skill.get(f"activationRatePermil{index}"),
                })
        return out, skill

    def _ability_effect_enchant(self, master: Any, effect_id: str) -> str | None:
        effects = _load_table(master, "produce_effects", "ProduceEffect")
        effect = _lookup(effects, effect_id)
        if effect is not None:
            direct = effect.get("produceExamStatusEnchantId")
            if direct:
                return str(direct)
        prefix = "p_effect-exam_status_enchant-"
        if str(effect_id).startswith(prefix):
            return str(effect_id)[len(prefix):]
        if str(effect_id).startswith("enchant-"):
            return str(effect_id)
        return None

    def _trigger_matches(self, trigger_id: str, step: ProduceStep) -> bool:
        kind = self._step_kind(step)
        if kind == "LESSON":
            text = f"{step.step_type} {step.step_id}".lower()
            parameter = self._lesson_parameter(step)
            name = {1: "vocal", 2: "dance", 3: "visual"}.get(parameter)
            if not name:
                return False
            expected = f"lesson_{name}"
            if " sp" in text or "_sp" in text:
                expected += "_sp"
            elif " hard" in text or "_hard" in text:
                expected += "_hard"
            tail = str(trigger_id).lower().split("p_trigger-start_lesson-", 1)[-1]
            return tail == expected
        return False

    @staticmethod
    def _skill_plan_type(skill: Mapping[str, Any] | None) -> int | None:
        if not skill:
            return None
        return _enum_number(skill.get("planType"))

    def activate_memory_abilities(
        self,
        master: Any,
        step_index: int,
        server_draw: ServerDraw | None = None,
    ) -> list[str]:
        """Roll matching memory abilities and return opening enchant ids."""

        cache = getattr(self, "_memory_ability_cache", None)
        if cache is None:
            cache = {}
            self._memory_ability_cache = cache
        cache_key = int(step_index)
        if cache_key in cache:
            return list(cache[cache_key])
        if server_draw is None:
            server_draw = ServerDraw(0, {})
        step = self.step(step_index)
        activated: list[str] = []
        for memory in self.memories:
            if not isinstance(memory, Mapping):
                continue
            payload = self._memory_payload(memory)
            user_memory_id = payload.get("user_memory_id") or memory.get("user_memory_id") or ""
            abilities = payload.get("abilities")
            if abilities is None:
                abilities = memory.get("abilities") or ()
            for ability in abilities:
                if isinstance(ability, Mapping):
                    ability_id = ability.get("id") or ability.get("ability_id")
                else:
                    ability_id = ability
                if not ability_id:
                    continue
                joins, skill = self._ability_joins(master, str(ability_id))
                plan_type = self._skill_plan_type(skill)
                if plan_type not in (None, _COMMON_PLAN_TYPE, self.identity.plan_type):
                    continue
                first_effect = skill.get("produceEffectId1") if skill else None
                first_trigger = skill.get("produceTriggerId1") if skill else None
                matching = None
                for join in joins:
                    if first_effect is not None and str(join.get("effect_id")) != str(first_effect):
                        continue
                    if first_trigger is not None and str(join.get("trigger_id")) != str(first_trigger):
                        continue
                    if self._trigger_matches(join.get("trigger_id", ""), step):
                        matching = join
                        break
                if matching is None:
                    continue
                if plan_type is None:
                    plan_type = _enum_number(matching.get("planType", matching.get("plan_type")))
                    if plan_type not in (None, _COMMON_PLAN_TYPE, self.identity.plan_type):
                        continue
                rate = matching.get("activation_rate_permil")
                if rate is None:
                    continue
                rate = int(rate)
                key = (str(user_memory_id), str(ability_id), step_index)
                hit = server_draw.resolve(
                    "memory_ability",
                    key,
                    roll=lambda rng, threshold=rate: rng.range(0, 1000) < threshold,
                )
                if not hit:
                    continue
                enchant_id = self._ability_effect_enchant(master, matching.get("effect_id", ""))
                if enchant_id:
                    activated.append(enchant_id)
        cache[cache_key] = list(activated)
        return activated

    def memory_ability_enchant_ids(self, master: Any, step_index: int, server_draw: ServerDraw | None = None) -> list[str]:
        return self.activate_memory_abilities(master, step_index, server_draw)

    def opening_enchant_ids(self, master: Any, step_index: int, server_draw: ServerDraw | None = None) -> list[str]:
        return self.item_enchant_ids(master) + self.activate_memory_abilities(master, step_index, server_draw)

    # -- provenance ----------------------------------------------------
    @staticmethod
    def _leaf_paths(value: Any, path: str) -> Iterable[str]:
        if is_dataclass(value):
            for item in fields(value):
                yield from ProduceRun._leaf_paths(getattr(value, item.name), f"{path}.{item.name}" if path else item.name)
            return
        if isinstance(value, Mapping):
            if not value:
                yield path
                return
            for key, child in value.items():
                yield from ProduceRun._leaf_paths(child, f"{path}[{key!r}]")
            return
        if isinstance(value, (list, tuple)):
            if not value:
                yield path
                return
            for index, child in enumerate(value):
                yield from ProduceRun._leaf_paths(child, f"{path}[{index}]")
            return
        yield path

    def provenance_paths(self) -> list[str]:
        paths: list[str] = []
        for item in fields(self):
            if item.name == "provenance":
                continue
            paths.extend(self._leaf_paths(getattr(self, item.name), item.name))
        return paths

    def provenance_errors(self) -> list[str]:
        errors = []
        for path in self.provenance_paths():
            entry = self.provenance.get(path)
            if not isinstance(entry, Mapping):
                errors.append(f"{path}: missing provenance")
                continue
            if entry.get("source") not in {"a", "b", "c"}:
                errors.append(f"{path}: source must be a/b/c")
            if not str(entry.get("locator") or "").strip():
                errors.append(f"{path}: empty locator")
        return errors

    def missing_provenance(self) -> list[str]:
        return [error.split(":", 1)[0] for error in self.provenance_errors()]

    def provenance_complete(self) -> bool:
        return not self.provenance_errors()

    def assert_provenance_complete(self) -> None:
        errors = self.provenance_errors()
        if errors:
            raise AssertionError("incomplete ProduceRun provenance: " + "; ".join(errors))

    def validate_provenance(self) -> None:
        self.assert_provenance_complete()

    def dump_provenance(self, path: str | Path) -> None:
        self.assert_provenance_complete()
        output = {key: self.provenance[key] for key in sorted(self.provenance)}
        with Path(path).open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(output, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")


__all__ = [
    "ProduceIdentity",
    "ProduceStep",
    "ProduceRun",
]
