# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""the loading of an exam -- the fields ExamState and SegmentDeck had when their constructors returned (engine/exam.py,
engine/deck.py), with no exam rule.
"""
import json
from pathlib import Path

from .card_data import RealRandomnessProvider, _top_tier, resolve_card_customizes


def _master_row(filename, fallback_id=None):
    """Read the packaged master row for callers constructing a bare ExamState."""
    path = Path(__file__).resolve().parents[1] / "ro" / "master" / "tables" / filename
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            row = json.loads(raw)
            decoded = row.get("decoded", row)
            if fallback_id is None or decoded.get("id") == fallback_id:
                return decoded
    return {}


DEFAULT_EXAM_SETTING = _master_row("ExamSetting.jsonl", "p_exam_setting-1")
DEFAULT_GLOBAL_SETTING = _master_row("Setting.jsonl")


class LoadingDeck:
    """SegmentDeck as its constructor leaves it, and its card resolution."""
    MOVE_TRIGGER_HAND = 5

    def __init__(
        self,
        cards,
        seed,
        turn_start_distribute=3,
        initial_card_id=None,
        initial_card_ids=None,
        resolve_initial_cards=False,
        segment_start_rng_advances=0,
        eligible_supports=(),
        upgrade_tiers=(),
        card_master=(),
        recycle_order="insertion",
        play_move_position_type_enabled=True,
        end_turn_lost_card_ids=(),
        audition_parameter_values=None,
        audition_limit_turn=None,
        audition_npc_rng_advances=0,
        card_customizes=(),
        card_grow_effects=(),
        randomness_provider=None,
    ):
        end_turn_lost_card_ids = set(end_turn_lost_card_ids)
        self.card_master = dict(card_master)
        self.card_customizes = dict(card_customizes)
        self.card_grow_effects = dict(card_grow_effects)
        self.deck = sorted((dict(card) for card in cards), key=lambda card: card["number"])
        for card in self.deck:
            card["temporary_upgrade_count"] = card.get(
                "temporary_upgrade_count", 0
            )
            card["support_upgrade_ids"] = list(card.get("support_upgrade_ids", ()))
            self.refresh_card(card)
            card["is_end_turn_lost"] = (
                card.get("is_end_turn_lost", False)
                or card["produce_card_id"] in end_turn_lost_card_ids
            )
        self.grave = []
        self.hand = []
        self.hold = []
        self.hold_limit = 2
        self.lost = []
        self.rng = randomness_provider if randomness_provider is not None else RealRandomnessProvider(seed)
        self.turn_start_distribute = turn_start_distribute
        self.hand_limit = 5
        if initial_card_ids is None and initial_card_id is not None:
            self.initial_card_ids = (initial_card_id,)
            self._initial_card_object_ids = None
        elif initial_card_ids is not None:
            self.initial_card_ids = tuple(initial_card_ids)
            self._initial_card_object_ids = None
        elif resolve_initial_cards:
            initial_cards = [
                card
                for card in self.deck
                if card.get("is_initial")
                or bool((card.get("resolved_card") or {}).get("isInitial"))
            ]
            self.initial_card_ids = tuple(
                card["produce_card_id"] for card in initial_cards
            )
            self._initial_card_object_ids = {id(card) for card in initial_cards}
        else:
            self.initial_card_ids = ()
            self._initial_card_object_ids = None
        self.initial_card_id = (
            initial_card_id
            if initial_card_id is not None
            else (self.initial_card_ids[0] if self.initial_card_ids else None)
        )
        self.segment_start_rng_advances = segment_start_rng_advances
        self.eligible_supports = [dict(support) for support in eligible_supports]
        for support in self.eligible_supports:
            if support["support_row_id"] == "s_card-3-0107":
                if "card_search_position_type" not in support:
                    support["card_search_position_type"] = 2
                if "parameter_type" not in support:
                    support["parameter_type"] = 1
        self.upgrade_tiers = frozenset(upgrade_tiers)
        self.recycle_order = recycle_order
        self.play_move_position_type_enabled = play_move_position_type_enabled
        self.is_hand_hold = False
        self.shuffle_sequence = []
        self.support_rolls = []
        self.pending_move_effects = []
        self.trigger_commands_enabled = True
        self.support_decisions = []
        self.generated_cards = []
        self.generated_card_count = 0
        self.rng_trace = []
        self.turn_used_support_ids = set()
        self.get_card_upgrade_count = 0
        self.current_turn = None
        self.current_parameter_type = None
        self.audition_parameter_values = audition_parameter_values
        self.audition_limit_turn = audition_limit_turn
        self.audition_npc_rng_advances = audition_npc_rng_advances
        self.turn_parameter_types = None
        self.initialized = False

    def resolved_view(self, card):
        """refresh_card's resolution, not written to the card -- what observe shows."""
        if not self.card_master:
            return card.get("resolved_card") or card
        count = (card["base_upgrade_count"] + card.get("temporary_upgrade_count", 0)
                 + len(card.get("support_upgrade_ids", ())))
        row = self.card_master.get((card["produce_card_id"], count))
        if row is None:
            row = self.card_master[(card["produce_card_id"], _top_tier(self.card_master, card["produce_card_id"], count))]
        resolved = resolve_card_customizes(card, row, self.card_customizes, self.card_grow_effects)
        if card.get("panic_stamina_fix") is not None:
            resolved["panic_stamina_fix"] = card["panic_stamina_fix"]
        return resolved

    def refresh_card(self, card):
        card["current_upgrade_count"] = (
            card["base_upgrade_count"]
            + card.get("temporary_upgrade_count", 0)
            + len(card.get("support_upgrade_ids", ()))
        )
        if self.card_master:
            resolved = self.card_master.get(
                (card["produce_card_id"], card["current_upgrade_count"])
            )
            if resolved is None:
                resolved = self.card_master[
                    (card["produce_card_id"], _top_tier(self.card_master, card["produce_card_id"],
                                                        card["current_upgrade_count"]))
                ]
            resolved_card = resolve_card_customizes(
                card,
                resolved,
                self.card_customizes,
                self.card_grow_effects,
            )
            if card.get("panic_stamina_fix") is not None:
                resolved_card["panic_stamina_fix"] = card["panic_stamina_fix"]
            card["resolved_card"] = resolved_card
            card["play_move_position_type"] = resolved_card[
                "playMovePositionType"
            ]["number"]
            in_lesson = int(card.get("temporary_upgrade_count", 0) or 0)
            for field in ("produceCardStatusEnchantId", "playProduceExamTriggerId"):
                if field == "produceCardStatusEnchantId" and in_lesson and field in card:
                    continue
                if field in resolved_card:
                    card[field] = resolved_card[field]
                else:
                    card.pop(field, None)
        return card.get("resolved_card")


class LoadingState:
    """ExamState as its constructor leaves it."""

    def __init__(
        self,
        stamina,
        score_limit,
        deck=None,
        card_searches=None,
        card_pools=None,
        plan_type=None,
        gimmick_rows=(),
        exam_effects=None,
        status_enchants=None,
        exam_triggers=None,
        trigger_effect_limits=None,
        exam_setting=None,
        setting=None,
        max_stamina=None,
    ):
        self.score = 0
        self.score_vocal = 0
        self.score_dance = 0
        self.score_visual = 0
        self.current_turn_total_add_parameter = 0
        self.stamina = stamina
        self.max_stamina = stamina if max_stamina is None else max_stamina
        self.block = 0
        self.addition_block = 0
        self.consumed_stamina = 0
        self.stamina_recover_add = 0
        self.stamina_recover_add_until_turn = None
        self.stamina_recover_restriction = False
        self.stamina_recover_restriction_until_turn = None
        self.lesson_buff = 0
        self.parameter_buff_until_turn = None
        self.parameter_buff_remaining = 0
        self.parameter_buff_passing_turn_start = False
        self.extra_turns = 0
        self.limit_turn = None
        self.pending_force_plays = []
        self.idol_unique_item_enchant_ids = frozenset()
        self.research_card_ids = frozenset()
        self.score_limit = score_limit
        self.turn = 0
        self.slump = False
        self.lesson_change_specify = None
        self.lesson_buff_multiple = 1.0
        self.lesson_debuff = 0
        self.enthusiastic_buff = 0
        self.enthusiastic_kept = []
        self.parameter_buff_multiple_per_turn = False
        self.gimmick_parameter_debuff = False
        self.review = 0
        self.review_additive_multiple = 1.0
        self.review_additive_fix = 0
        self.parameter_buff_additive_multiple = 1.0
        self.parameter_buff_additive_fix = 0
        self.lesson_buff_additive_multiple = 1.0
        self.lesson_buff_additive_fix = 0
        self.score_trace = None
        self.gain_split = None
        self.aggressive_additive_multiple = 1.0
        self.aggressive_additive_fix = 0
        self.review_multiple = 1.0
        self._timed_gain_layers = {}
        self.review_count_add = 0
        self.review_count_add_until_turn = None
        self.review_count_add_layers = []
        self.review_turn_end_reduce_lock_turn = 0
        self.review_turn_start = 0
        self.review_added_this_turn = 0
        self.review_events = []
        self.review_consumption_sum = 0
        self.aggressive = 0
        self.lesson_parameter_multiple_depend_review_or_aggressive = False
        self.lesson_parameter_multiple_depend_until_turn = None
        self.lesson_parameter_multiple = 1.0
        self.lesson_parameter_multiple_until_turn = None
        self.lesson_parameter_multiple_layers = []
        self.lesson_buff_multiple_layers = []
        self.review_multiple_layers = []
        self.lesson_parameter_multiple_down_layers = []
        self.full_power_lesson_multiple_additive_layers = []
        self.full_power_lesson_multiple_additive = 0.0
        self.concentration_lesson_multiple_additive_layers = []
        self.concentration_lesson_multiple_additive = 0.0
        self.lesson_parameter_multiple_down = 0.0
        self.idol_status_type = None
        self.idol_status_step_count = 0
        self.full_power_point = 0
        self.full_power_point_sum = 0
        self.full_power_point_additive_permil = 0
        self.full_power_point_additive_layers = []
        self.enthusiastic_multiple_permil = 0
        self.enthusiastic_additive_layers = []
        self.stance_lock_turns = 0
        self.stance_change_count = 0
        self.direct_stance_change_count = 0
        self.stance_lock_passing_turn_start = False
        self.stance_events = []
        self.concentration_change_count = 0
        self.preservation_change_count = 0
        self.full_power_change_count = 0
        self._wire_snapshot_turn = False
        self._in_turn_start = False
        self._indirect_effect_depth = 0
        self._effect_permil_stack = []
        self.over_preservation_lesson_multiplier = None
        self.over_preservation_stamina_multiplier = None
        self.block_restriction = False
        self.block_restriction_until_turn = None
        self.block_add_down = False
        self.block_add_down_restriction = False
        self.block_add_down_until_turn = None
        self.slump_until_turn = None
        self.gimmick_parameter_debuff_until_turn = None
        self.lesson_debuff_layers = []
        self.stamina_consumption_add_fix_layers = []
        self.card_draw_down_layers = []
        self.play_card_limits = []
        self.block_add_down_fix = 0
        self.block_add_down_fix_restriction = False
        self.stamina_consumption_down = False
        self.stamina_consumption_down_until_turn = None
        self.stamina_consumption_down_add = False
        self.stamina_consumption_add = False
        self.stamina_consumption_add_active_from_turn = None
        self.stamina_consumption_add_until_turn = None
        self.stamina_consumption_add_down = False
        self.stamina_consumption_add_fix = 0
        self.buff_consumption_add = False
        self.buff_consumption_add_active_from_turn = None
        self.buff_consumption_add_until_turn = None
        self.stamina_consumption_down_fix = 0
        self.stamina_reduce_change = False
        self.stamina_reduce_change_threshold = 0
        self.parameter_buff_multiple_until_turn = None
        self.playable = 1
        self.playable_add_stock = 0
        self.playable_add_passing = False
        self.turn_playable_base = 1
        self.turn_playable_adds = 0
        self.active_enchants = []
        self.trigger_effect_states = []
        self.trigger_events = []
        self.exam_setting = (
            DEFAULT_EXAM_SETTING if exam_setting is None else exam_setting
        )
        self.setting = DEFAULT_GLOBAL_SETTING if setting is None else setting
        self.play_count = 0
        self._card_own_effect_depth = 0
        self.block_consumption_sum = 0
        self.current_parameter_type = None
        self.oikomi_threshold = None
        self.anti_debuff = 0
        self.debuff_since = {}
        self._debuff_seq = 0
        self.deck = deck
        self.card_searches = card_searches or {}
        self.card_pools = card_pools or {}
        self.plan_type = plan_type
        self.selected_card_numbers = ()
        self.select_index_queue = []
        self.playing_card = None
        self.target_card = None
        self.unresolved_effects = []
        self.panic_until_turn = None
        self.unresolved_statuses = []
        self.gimmick_rows = sorted(
            (dict(row) for row in gimmick_rows),
            key=lambda row: (int(row["startTurn"]), int(row["priority"])),
        )
        self.exam_effects = exam_effects
        self.status_enchants = status_enchants or {}
        self.exam_triggers = exam_triggers or {}
        self.trigger_effect_limits = trigger_effect_limits or {}
        self.trigger_effect_spent_totals = {}
        self.gimmick_events = []
        self.pre_applied_gimmick_rows = set()
        self.gimmick_lb_proxy_used = []
        self.play_card_cost_changes = []
        self.effect_timer_queue = {}
        self.end_turn_timer_queue = {}
        self.turn_play_count = 0
        self.pending_trigger_installs = []
        self._install_origin = None
        self._exam_start_dispatched = False
        self.effect_play_count_buffs = []
        self.current_card_effect_repeat = 0
        self.last_played_card_category = None
