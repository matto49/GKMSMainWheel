# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""the native state layout (single source of truth)."""
from __future__ import annotations

import ctypes as C
import math
import re

NONE_I64 = -(2 ** 63)
LAYOUT_VERSION = 20

MAX_CARDS = 64
MAX_ZONE = 64
MAX_TES = 48
MAX_EFFECT_IDS = 16
MAX_ENCHANTS = 48
MAX_MAP = 48
MAX_SMALL = 8
MAX_PENDING_MOVE = 8
MAX_TUPLE = 5
MAX_LAYERS = 64
MAX_TG_LAYERS = 16
MAX_TIMER_IDS = 16
MAX_CUSTOMIZES = 4
MAX_GROWS = 256
MAX_SUPPORT_UP = 4
MAX_TURN_TYPES = 32
ZONES = ("deck", "hand", "grave", "hold", "lost")


class Unsupported(Exception):
    """A portable state the native layout does not represent."""


class Num(C.Structure):
    _fields_ = [("i", C.c_int64), ("f", C.c_double), ("tag", C.c_uint8)]


class CardNo(C.Structure):
    _fields_ = [("n", C.c_int32), ("g", C.c_uint8)]


class GrowRegistry(C.Structure):
    _fields_ = [("number", CardNo), ("n", C.c_int32), ("ids", C.c_int32 * MAX_GROWS)]


class Card(C.Structure):
    _fields_ = [
        ("number", CardNo),
        ("guid", CardNo),
        ("produce_card_id", C.c_int32),
        ("base_upgrade_count", C.c_int32),
        ("current_upgrade_count", C.c_int32),
        ("temporary_upgrade_count", C.c_int32),
        ("n_support_upgrade_ids", C.c_int32),
        ("support_upgrade_ids", C.c_int32 * MAX_SUPPORT_UP),
        ("play_move_position_type", C.c_int32),
        ("is_end_turn_lost", C.c_uint8),
        ("has_customizes", C.c_uint8),
        ("n_customizes", C.c_int32),
        ("customize_id", C.c_int32 * MAX_CUSTOMIZES),
        ("customize_count", C.c_int32 * MAX_CUSTOMIZES),
        ("has_origin_type", C.c_uint8),
        ("origin_type", C.c_int32),
        ("has_is_initial", C.c_uint8),
        ("is_initial", C.c_uint8),
        ("grave_source", C.c_int32),
        ("play_trigger_id", C.c_int32),
        ("type9_moved", C.c_int8),
        ("panic_stamina_fix", C.c_int64),
        ("has_panic_stamina_fix", C.c_uint8),
        ("panic_stamina_until_turn", C.c_int64),
        ("has_panic_stamina_until_turn", C.c_uint8),
        ("card_status_enchant", C.c_int32),
        ("has_card_status_enchant", C.c_uint8),
        ("force_pays_cost", C.c_int8),
        ("has_grows", C.c_uint8), ("n_grows", C.c_int32), ("grows", C.c_int32 * MAX_GROWS),
        ("has_enchant_fired", C.c_uint8), ("enchant_fired", C.c_int64),
        ("has_stance_count_base", C.c_uint8), ("stance_count_base", C.c_int64),
        ("fusion_top1", C.c_int16), ("p4_group", C.c_int8),
    ]


CARD_KEYS = {"number", "guid", "produce_card_id", "base_upgrade_count", "current_upgrade_count",
             "temporary_upgrade_count", "support_upgrade_ids", "play_move_position_type",
             "is_end_turn_lost", "customizes", "origin_type", "is_initial", "_grave_source",
             "playProduceExamTriggerId", "_type9_moved_from_playing",
             "panic_stamina_fix", "panic_stamina_until_turn",
             "produceCardStatusEnchantId", "_force_play_pays_cost", "dynamic_grow_effect_ids", "card_enchant_fired",
             "stance_count_base"}


class CardEnchantSpec(C.Structure):
    _fields_ = [("id", C.c_int32), ("trigger", C.c_int32), ("limit", C.c_int64),
                ("n_grow", C.c_int32), ("grows", C.c_int32 * MAX_EFFECT_IDS)]


class TES(C.Structure):
    _fields_ = [
        ("enchant_id", C.c_int32),
        ("origin", C.c_int32),
        ("trigger", C.c_int32),
        ("n_effect_ids", C.c_int32),
        ("effect_ids", C.c_int32 * MAX_EFFECT_IDS),
        ("installed_play_count", C.c_int64),
        ("limit_count", C.c_int64),
        ("limit_count_in_turn", C.c_int64),
        ("spent_total", C.c_int64),
        ("spent_in_turn", C.c_int64),
        ("phase_count", C.c_int64),
        ("remaining_turns", C.c_int64),
        ("passing_turn_start", C.c_uint8),
        ("skip_play_count_once", C.c_uint8),
        ("skip_card_played_once", C.c_uint8),
        ("has_interval44", C.c_uint8),
        ("interval44_count", C.c_int64),
        ("interval53_count", C.c_int64),
        ("interval52_count", C.c_int64),
        ("interval22_origin", C.c_int64),
        ("status_change_fired_turn", C.c_int64),
        ("owner_card", C.c_int32),
    ]


TES_INT = ("installed_play_count", "limit_count", "limit_count_in_turn", "spent_total",
           "spent_in_turn", "phase_count")
TES_BOOL = ("passing_turn_start", "skip_play_count_once", "skip_card_played_once")


class TupleList(C.Structure):
    _fields_ = [("n", C.c_int32), ("len", C.c_int32 * MAX_LAYERS), ("v", (Num * MAX_TUPLE) * MAX_LAYERS)]


class StrIntMap(C.Structure):
    _fields_ = [("n", C.c_int32), ("key", C.c_int32 * MAX_MAP), ("val", C.c_int64 * MAX_MAP)]


class SmallStrIntMap(C.Structure):
    _fields_ = [("n", C.c_int32), ("key", C.c_int32 * MAX_MAP), ("val", C.c_int64 * MAX_MAP)]


class TimedGain(C.Structure):
    _fields_ = [("n", C.c_int32), ("name", C.c_int32 * MAX_SMALL), ("nl", C.c_int32 * MAX_SMALL),
                ("expire", (C.c_int64 * MAX_TG_LAYERS) * MAX_SMALL),
                ("value", (Num * MAX_TG_LAYERS) * MAX_SMALL),
                ("fresh", (C.c_int64 * MAX_TG_LAYERS) * MAX_SMALL)]


class PlayCountBuff(C.Structure):
    _fields_ = [("search_id", C.c_int32), ("repeat_count", C.c_int64), ("remaining_uses", C.c_int64),
                ("until_turn", C.c_int64)]


class CostChange(C.Structure):
    _fields_ = [("value", C.c_int64), ("remaining", C.c_int64), ("search_id", C.c_int32)]


class TimerQueue(C.Structure):
    _fields_ = [("n", C.c_int32), ("turn", C.c_int64 * MAX_SMALL), ("nids", C.c_int32 * MAX_SMALL),
                ("ids", (C.c_int32 * MAX_TIMER_IDS) * MAX_SMALL)]


class SelectQueue(C.Structure):
    _fields_ = [("n", C.c_int32), ("len", C.c_int32 * MAX_SMALL), ("v", (C.c_int64 * MAX_SMALL) * MAX_SMALL)]


# ---------------------------------------------------------------------------
# kind: i64 | bool | f64 | opt_i64 (None|int) | status (stance enum)
# ---------------------------------------------------------------------------
STATE_SCALARS = (
    ("_debuff_seq", "i64"), ("_exam_start_dispatched", "bool"), ("_in_turn_start", "bool"),
    ("_in_status_change_trigger", "bool", True),
    ("_indirect_effect_depth", "i64"), ("_lpm_status_seq", "i64", True), ("_wire_snapshot_turn", "bool"),
    ("addition_block", "i64"), ("aggressive", "i64"), ("aggressive_additive_fix", "i64"),
    ("aggressive_additive_multiple", "f64"), ("anti_debuff", "i64"), ("block", "i64"),
    ("block_add_down", "bool"), ("block_add_down_fix", "i64"), ("block_add_down_fix_restriction", "bool"),
    ("block_add_down_until_turn", "opt_i64", True),
    ("block_add_down_restriction", "bool"), ("block_consumption_sum", "i64"), ("block_restriction", "bool"),
    ("block_restriction_until_turn", "opt_i64"),
    ("buff_consumption_add", "bool"), ("buff_consumption_add_active_from_turn", "opt_i64"),
    ("buff_consumption_add_until_turn", "opt_i64"),
    ("client_turn_end_order", "bool"),
    ("concentration_change_count", "i64"),
    ("concentration_lesson_multiple_additive", "f64"), ("consumed_stamina", "i64"), ("current_card_effect_repeat", "i64"),
    ("direct_stance_change_count", "i64"),
    ("current_parameter_type", "i64"), ("current_turn_total_add_parameter", "i64"),
    ("enthusiastic_buff", "i64"), ("enthusiastic_multiple_permil", "i64"), ("extra_turns", "i64"),
    ("full_power_change_count", "i64"), ("full_power_lesson_multiple_additive", "f64"),
    ("over_preservation_lesson_multiplier", "opt_f64", True), ("over_preservation_stamina_multiplier", "opt_f64", True),
    ("full_power_point", "i64"), ("full_power_point_additive_permil", "i64"), ("full_power_point_sum", "i64"),
    ("gimmick_parameter_debuff", "bool"), ("gimmick_parameter_debuff_until_turn", "opt_i64", True),
    ("idol_status_type", "status"), ("idol_status_step_count", "i64"),
    ("last_played_card_category", "opt_i64"), ("lesson_buff", "i64"), ("lesson_buff_additive_fix", "i64"),
    ("lesson_buff_additive_multiple", "f64"), ("lesson_buff_multiple", "f64"), ("lesson_debuff", "i64"),
    ("lesson_parameter_multiple", "f64"), ("lesson_parameter_multiple_depend_review_or_aggressive", "bool"),
    ("lesson_change_specify", "opt_i64", True),
    ("lesson_parameter_multiple_depend_until_turn", "opt_i64", True),
    ("lesson_parameter_multiple_down", "f64"), ("lesson_parameter_multiple_until_turn", "opt_i64"),
    ("limit_turn", "i64"), ("max_stamina", "i64"), ("oikomi_threshold", "opt_i64", True),
    ("parameter_buff_additive_fix", "i64"),
    ("parameter_buff_additive_multiple", "f64"), ("parameter_buff_multiple_per_turn", "bool"),
    ("parameter_buff_multiple_until_turn", "opt_i64"), ("parameter_buff_passing_turn_start", "bool"),
    ("parameter_buff_remaining", "i64"), ("parameter_buff_until_turn", "opt_i64"), ("plan_type", "i64"),
    ("play_count", "i64"), ("playable", "i64"), ("playable_add_stock", "i64"), ("playable_add_passing", "bool"),
    ("preservation_change_count", "i64"), ("review", "i64"), ("review_added_this_turn", "i64"),
    ("review_additive_fix", "i64"), ("review_additive_multiple", "f64"), ("review_consumption_sum", "i64"),
    ("review_count_add", "i64"), ("review_multiple", "f64"), ("review_turn_end_reduce_lock_turn", "i64"),
    ("review_turn_start", "i64"), ("review_count_add_until_turn", "opt_i64", True),
    ("score", "i64"), ("score_dance", "i64"), ("score_limit", "opt_i64", True),
    ("score_visual", "i64"), ("score_vocal", "i64"), ("slump", "bool"),
    ("slump_until_turn", "opt_i64", True), ("stamina", "i64"), ("stamina_consumption_add", "bool"),
    ("stamina_consumption_add_active_from_turn", "opt_i64"), ("stamina_consumption_add_down", "bool"),
    ("stamina_consumption_add_fix", "i64"), ("stamina_consumption_add_until_turn", "opt_i64"),
    ("stamina_consumption_down", "bool"), ("stamina_consumption_down_add", "bool"),
    ("stamina_consumption_down_fix", "i64"), ("stamina_consumption_down_until_turn", "opt_i64"),
    ("stamina_recover_add", "i64"), ("stamina_recover_add_until_turn", "opt_i64", True),
    ("stamina_recover_restriction", "bool"),
    ("stamina_recover_restriction_until_turn", "opt_i64", True), ("stamina_reduce_change", "bool"),
    ("stamina_reduce_change_threshold", "i64"), ("stance_change_count", "i64"),
    ("stance_lock_passing_turn_start", "bool"), ("stance_lock_turns", "i64"), ("turn", "i64"),
    ("turn_play_count", "i64"), ("turn_playable_adds", "i64"), ("turn_playable_base", "i64"),
    ("turn_playable_start", "i64"), ("panic_until_turn", "opt_i64", True),
)
# constant at boundaries in H.I.F: (name, value in decoded form)
STATE_CONSTS = (
    ("_install_origin", None),
    ("_effect_permil_stack", []), ("card_draw_down_layers", []), ("enthusiastic_additive_layers", []),
    ("full_power_lesson_multiple_additive_layers", []), ("full_power_point_additive_layers", []),
    ("lesson_debuff_layers", []), ("lesson_parameter_multiple_down_layers", []),
    ("pending_force_plays", []), ("pending_trigger_installs", []), ("play_card_limits", []),
    ("pre_applied_gimmick_rows", ("$set", [])), ("selected_card_numbers", []),
    ("setting", ("$ref", "master|setting")),
    ("_card_own_effect_depth", 0),
)
STATE_CONSTS = tuple(x for x in STATE_CONSTS if x[0] not in {
    "card_draw_down_layers", "enthusiastic_additive_layers",
    "full_power_lesson_multiple_additive_layers", "full_power_point_additive_layers",
    "lesson_debuff_layers", "lesson_parameter_multiple_down_layers", "play_card_limits",
    "pending_force_plays", "pending_trigger_installs",
})
STATE_TUPLE_LISTS = (
    "lesson_buff_multiple_layers", "lesson_parameter_multiple_layers", "review_multiple_layers",
    "stamina_consumption_add_fix_layers", "card_draw_down_layers", "lesson_debuff_layers",
    "lesson_parameter_multiple_down_layers", "enthusiastic_additive_layers",
    "full_power_lesson_multiple_additive_layers", "full_power_point_additive_layers",
    "play_card_limits",
    "review_count_add_layers",
    "concentration_lesson_multiple_additive_layers",
    "enthusiastic_kept",
)

_SCALAR_CTYPE = {"i64": C.c_int64, "bool": C.c_uint8, "f64": C.c_double,
                 "opt_i64": C.c_int64, "opt_f64": C.c_double, "status": C.c_int32}

_STATUS_TO_CODE = {None: 0, "Concentration": 1, "Preservation": 2, "FullPower": 3,
                   "Leisurely": 4, "OverPreservation": 5}
_CODE_TO_STATUS = {v: k for k, v in _STATUS_TO_CODE.items()}


def _state_fields():
    f = []
    for spec in STATE_SCALARS:
        name, kind = spec[0], spec[1]
        f.append((name, _SCALAR_CTYPE[kind]))
        if len(spec) > 2:
            f.append(("has_" + name, C.c_uint8))
    f += [
        ("has_card_enchant_specs", C.c_uint8), ("n_card_enchant_specs", C.c_int32),
        ("card_enchant_specs", CardEnchantSpec * MAX_ENCHANTS),
        ("timed_gain_layers", TimedGain),
        ("n_active_enchants", C.c_int32), ("active_enchants", C.c_int32 * MAX_ENCHANTS),
        ("debuff_since", SmallStrIntMap),
        ("n_effect_play_count_buffs", C.c_int32), ("effect_play_count_buffs", PlayCountBuff * MAX_SMALL),
        ("effect_timer_queue", TimerQueue),
        ("end_turn_timer_queue", TimerQueue),
        ("lesson_buff_multiple_layers", TupleList),
        ("lesson_parameter_multiple_layers", TupleList),
        ("review_multiple_layers", TupleList),
        ("stamina_consumption_add_fix_layers", TupleList),
        ("card_draw_down_layers", TupleList),
        ("lesson_debuff_layers", TupleList),
        ("lesson_parameter_multiple_down_layers", TupleList),
        ("enthusiastic_additive_layers", TupleList),
        ("full_power_lesson_multiple_additive_layers", TupleList),
        ("full_power_point_additive_layers", TupleList),
        ("play_card_limits", TupleList),
        ("review_count_add_layers", TupleList),
        ("concentration_lesson_multiple_additive_layers", TupleList),
        ("enthusiastic_kept", TupleList),
        ("n_play_card_cost_changes", C.c_int32), ("play_card_cost_changes", CostChange * MAX_SMALL),
        ("playing_card", C.c_int32),
        ("n_force", C.c_int32), ("force", C.c_int16 * MAX_CARDS),
        ("force_has_resolved", C.c_uint8 * MAX_CARDS),
        ("n_pending_installs", C.c_int32), ("pending_installs", C.c_int32 * MAX_ENCHANTS),
        ("select_index_queue", SelectQueue),
        ("trigger_effect_limits", StrIntMap),
        ("trigger_effect_spent_totals", StrIntMap),
        ("n_tes", C.c_int32), ("tes", TES * MAX_TES),
        ("n_turn_start_hand_numbers", C.c_int32), ("turn_start_hand_numbers", CardNo * MAX_SMALL),
        ("fusion_delta", C.c_int64 * 4), ("meta_stage", C.c_int64), ("meta_before_turns", C.c_int64),
    ]
    return f


class ExamStateP(C.Structure):
    _fields_ = _state_fields()


class GkmsState(C.Structure):
    _fields_ = [
        ("magic", C.c_uint32), ("version", C.c_uint32), ("round_seed", C.c_int64),
        ("s_turn", C.c_int64), ("s_finished", C.c_uint8), ("s_cleared", C.c_uint8),
        ("n_drinks", C.c_int32), ("drinks", C.c_int32 * MAX_SMALL),
        ("rng", C.c_uint32),
        ("d_initialized", C.c_uint8), ("d_current_turn", C.c_int64), ("d_current_parameter_type", C.c_int64),
        ("d_has_turn_parameter_types", C.c_uint8),
        ("d_n_turn_parameter_types", C.c_int32), ("d_turn_parameter_types", C.c_int64 * MAX_TURN_TYPES),
        ("d_n_turn_used_support_ids", C.c_int32), ("d_turn_used_support_ids", C.c_int32 * MAX_SMALL),
        ("d_get_card_upgrade_count", C.c_int64), ("d_generated_card_count", C.c_int64),
        ("d_is_hand_hold", C.c_uint8), ("d_hold_limit", C.c_int64), ("d_hand_limit", C.c_int64),
        ("d_turn_start_distribute", C.c_int64),
        ("d_has_grow_registry", C.c_uint8), ("d_n_grow_registry", C.c_int32),
        ("d_grow_registry", GrowRegistry * MAX_CARDS),
        ("d_has_pending_move", C.c_uint8), ("d_n_pending_move", C.c_int32),
        ("d_pending_move_slot", C.c_int16 * MAX_PENDING_MOVE),
        ("d_pending_move_n", C.c_int32 * MAX_PENDING_MOVE), ("d_pending_move_eff", (C.c_int32 * 4) * MAX_PENDING_MOVE),
        ("p_stamina", C.c_int64), ("p_max_stamina", C.c_int64),
        ("n_cards", C.c_int32), ("cards", Card * MAX_CARDS),
        ("zone_n", C.c_int32 * 7), ("zone", (C.c_int16 * MAX_ZONE) * 7),
        ("st", ExamStateP),
    ]


MAGIC = 0x534D4B47
DECK_KEYS = ("initialized", "current_turn", "current_parameter_type", "turn_parameter_types",
             "turn_used_support_ids", "get_card_upgrade_count", "generated_card_count", "is_hand_hold",
             "hold_limit", "hand_limit", "turn_start_distribute", "dynamic_grow_by_number", "pending_move_effects")


# ---------------------------------------------------------------------------
# string table
# ---------------------------------------------------------------------------
class Ctx:
    def __init__(self, strings, round_seed=0, master=None):
        self.strings = list(strings)
        self.index = {s: i for i, s in enumerate(self.strings)}
        self.round_seed = round_seed
        self.master = master

    def sid(self, s):
        if s is None:
            return -1
        i = self.index.get(s)
        if i is None:
            raise Unsupported(f"string not in table: {s!r}")
        return i

    def s(self, i):
        return None if i < 0 else self.strings[i]


_G = re.compile(r"^G(\d+)$")
_SC = re.compile(r"^scenario-(-?\d+)-(\d+)$")
_PREFIXED = re.compile(r"^(.*?-)(\d+)$")
GUID_TABLE = 40


def _cardno_pack(v, dst, ctx, what):
    if isinstance(v, bool):
        raise Unsupported(f"{what}: bool")
    if isinstance(v, int):
        dst.n, dst.g = v, 0
        return
    if isinstance(v, str):
        m = _G.match(v)
        if m:
            dst.n, dst.g = int(m.group(1)), 1
            return
    raise Unsupported(f"{what}: {v!r}")


def _cardno_unpack(src):
    return f"G{src.n}" if src.g else int(src.n)


def _guid_pack(v, dst, ctx):
    m = _G.match(v)
    if m:
        dst.n, dst.g = int(m.group(1)), 1
        return
    m = _SC.match(v)
    if m and int(m.group(1)) == ctx.round_seed:
        dst.n, dst.g = int(m.group(2)), 0
        return
    m = _PREFIXED.match(v)
    if m and str(int(m.group(2))) == m.group(2) and int(m.group(2)) < 2 ** 31:
        prefixes = ctx.__dict__.setdefault("guid_prefixes", [])
        if m.group(1) not in prefixes:
            if len(prefixes) >= 30:
                raise Unsupported(f"guid prefixes: {v!r}")
            prefixes.append(m.group(1))
        dst.n, dst.g = int(m.group(2)), 2 + prefixes.index(m.group(1))
        return
    if isinstance(v, str) and v:
        table = ctx.__dict__.setdefault("guid_table", [])
        if v not in table:
            table.append(v)
        dst.n, dst.g = table.index(v), GUID_TABLE
        return
    raise Unsupported(f"guid {v!r}")


def _guid_unpack(src, ctx):
    if src.g == GUID_TABLE:
        table = ctx.__dict__.get("guid_table", [])
        if src.n >= len(table):
            raise Unsupported(f"guid table index: {src.n}")
        return table[src.n]
    if src.g >= 2:
        prefixes = ctx.__dict__.get("guid_prefixes", [])
        if src.g - 2 >= len(prefixes):
            raise Unsupported(f"guid prefix index: {src.g}")
        return f"{prefixes[src.g - 2]}{src.n}"
    return f"G{src.n}" if src.g else f"scenario-{ctx.round_seed}-{src.n}"


def _num_pack(v, dst):
    if v is None:
        dst.tag, dst.i, dst.f = 2, 0, 0.0
    elif isinstance(v, bool):
        raise Unsupported("bool in Num")
    elif isinstance(v, int):
        dst.tag, dst.i, dst.f = 0, v, 0.0
    elif isinstance(v, float):
        dst.tag, dst.i, dst.f = 1, 0, v
    else:
        raise Unsupported(f"Num {v!r}")


def _num_unpack(src):
    return None if src.tag == 2 else (float(src.f) if src.tag == 1 else int(src.i))


def _opt(v):
    if v is None:
        return NONE_I64
    if isinstance(v, bool) or not isinstance(v, int):
        raise Unsupported(f"opt int {v!r}")
    return v


def _timer_id(ctx, v):
    """a negative timer id is an indirect timer (~sid -> \"~\" + id)."""
    v = int(v)
    return "~" + ctx.s(~v) if v < 0 else ctx.s(v)


def _unopt(v):
    return None if v == NONE_I64 else int(v)


def _rec(v, what):
    if not (isinstance(v, tuple) and v and v[0] == "$rec"):
        raise Unsupported(f"{what}: expected record")
    return dict(v[1])


def _map(v, what):
    if not (isinstance(v, tuple) and v and v[0] == "$map"):
        raise Unsupported(f"{what}: expected map")
    return v[1]


def _cap(n, cap, what):
    if n > cap:
        raise Unsupported(f"{what}: {n} > capacity {cap}")


def _int(v, what):
    if isinstance(v, bool) or not isinstance(v, int):
        raise Unsupported(f"{what}: {v!r}")
    return v


def _bool(v, what):
    if not isinstance(v, bool):
        raise Unsupported(f"{what}: {v!r}")
    return v


# ---------------------------------------------------------------------------
# pack
# ---------------------------------------------------------------------------
def _pack_card(rec, dst, ctx):
    extra = set(rec) - CARD_KEYS
    if extra:
        raise Unsupported(f"card keys {sorted(extra)}")
    _cardno_pack(rec["number"], dst.number, ctx, "number")
    _guid_pack(rec["guid"], dst.guid, ctx)
    dst.produce_card_id = ctx.sid(rec["produce_card_id"])
    for k in ("base_upgrade_count", "current_upgrade_count", "temporary_upgrade_count",
              "play_move_position_type"):
        setattr(dst, k, _int(rec[k], k))
    sup = rec["support_upgrade_ids"]
    _cap(len(sup), MAX_SUPPORT_UP, "support_upgrade_ids")
    dst.n_support_upgrade_ids = len(sup)
    for i, x in enumerate(sup):
        dst.support_upgrade_ids[i] = ctx.sid(x)
    dst.is_end_turn_lost = _bool(rec["is_end_turn_lost"], "is_end_turn_lost")
    if "customizes" in rec:
        cz = rec["customizes"]
        if not isinstance(cz, list):
            raise Unsupported("customizes not list")
        _cap(len(cz), MAX_CUSTOMIZES, "customizes")
        dst.has_customizes, dst.n_customizes = 1, len(cz)
        for i, c in enumerate(cz):
            c = _rec(c, "customize")
            if set(c) == {"id", "count"}:
                c = {"id": c["id"], "customize_count": c["count"]}
            if set(c) != {"id", "customize_count"}:
                raise Unsupported(f"customize keys {sorted(c)}")
            dst.customize_id[i] = ctx.sid(c["id"])
            dst.customize_count[i] = _int(c["customize_count"], "customize_count")
    if rec.get("origin_type") is not None:
        dst.has_origin_type, dst.origin_type = 1, _int(rec["origin_type"], "origin_type")
    if "is_initial" in rec:
        dst.has_is_initial, dst.is_initial = 1, _bool(rec["is_initial"], "is_initial")
    dst.grave_source = ctx.sid(rec["_grave_source"]) if "_grave_source" in rec else -1
    if "_grave_source" in rec and rec["_grave_source"] is None:
        raise Unsupported("_grave_source None")
    dst.play_trigger_id = ctx.sid(rec["playProduceExamTriggerId"]) if "playProduceExamTriggerId" in rec else -1
    dst.has_card_status_enchant = int("produceCardStatusEnchantId" in rec)
    dst.card_status_enchant = ctx.sid(rec.get("produceCardStatusEnchantId"))
    dst.force_pays_cost = int(_bool(rec["_force_play_pays_cost"], "force_pays_cost")) if "_force_play_pays_cost" in rec else -1
    dst.has_grows = int("dynamic_grow_effect_ids" in rec)
    grows = rec.get("dynamic_grow_effect_ids", [])
    _cap(len(grows), MAX_GROWS, "dynamic_grow_effect_ids")
    dst.n_grows = len(grows)
    for i, gid in enumerate(grows): dst.grows[i] = ctx.sid(gid)
    dst.has_enchant_fired = int("card_enchant_fired" in rec)
    dst.enchant_fired = _int(rec.get("card_enchant_fired", 0), "card_enchant_fired")
    dst.has_stance_count_base = int("stance_count_base" in rec)
    dst.stance_count_base = _int(rec.get("stance_count_base", 0), "stance_count_base")
    dst.type9_moved = (int(_bool(rec["_type9_moved_from_playing"], "type9"))
                       if "_type9_moved_from_playing" in rec else -1)
    if "panic_stamina_fix" in rec:
        dst.has_panic_stamina_fix, dst.panic_stamina_fix = 1, _int(rec["panic_stamina_fix"], "panic_stamina_fix")
    if "panic_stamina_until_turn" in rec:
        dst.has_panic_stamina_until_turn, dst.panic_stamina_until_turn = 1, _int(rec["panic_stamina_until_turn"], "panic_stamina_until_turn")


def _pack_tuple_list(v, dst, what):
    if not isinstance(v, list):
        raise Unsupported(what)
    _cap(len(v), MAX_LAYERS, what)
    dst.n = len(v)
    for i, t in enumerate(v):
        if not isinstance(t, list):
            raise Unsupported(f"{what}[{i}] not tuple")
        _cap(len(t), MAX_TUPLE, what)
        dst.len[i] = len(t)
        for j, x in enumerate(t):
            _num_pack(x, dst.v[i][j])


def _pack_state_tuple_list(name, v, dst, ctx):
    """Pack a numeric state layer, preserving the native list shape."""
    if name == "play_card_limits":
        if not isinstance(v, list):
            raise Unsupported(name)
        _cap(len(v), MAX_LAYERS, name)
        dst.n = len(v)
        for i, row in enumerate(v):
            if not isinstance(row, list) or len(row) != 2:
                raise Unsupported(f"{name}[{i}]")
            dst.len[i] = 2
            dst.v[i][0].tag, dst.v[i][0].i, dst.v[i][0].f = 0, ctx.sid(row[0]), 0.0
            _num_pack(row[1], dst.v[i][1])
        return
    if name in {"enthusiastic_additive_layers", "full_power_point_additive_layers"}:
        if not isinstance(v, list):
            raise Unsupported(name)
        _cap(len(v), MAX_LAYERS, name)
        dst.n = len(v)
        for i, row in enumerate(v):
            row = _rec(row, f"{name}[{i}]")
            if name == "full_power_point_additive_layers":
                if set(row) != {"turns", "permil", "passing"}:
                    raise Unsupported(f"{name}[{i}] keys")
                vals = (row["turns"], row["permil"], int(_bool(row["passing"], "passing")))
            else:
                if set(row) - {"fresh"} != {"value", "until_turn"}:
                    raise Unsupported(f"{name}[{i}] keys")
                vals = (row["value"], row["until_turn"]) + ((row["fresh"],) if "fresh" in row else ())
            dst.len[i] = len(vals)
            for j, x in enumerate(vals):
                _num_pack(x, dst.v[i][j])
        return
    _pack_tuple_list(v, dst, name)


def _unpack_state_tuple_list(name, src, ctx):
    if name == "play_card_limits":
        return [[ctx.s(int(src.v[i][0].i)), _num_unpack(src.v[i][1])] for i in range(src.n)]
    if name in {"enthusiastic_additive_layers", "full_power_point_additive_layers"}:
        out = []
        for i in range(src.n):
            vals = [_num_unpack(src.v[i][j]) for j in range(src.len[i])]
            if name == "full_power_point_additive_layers":
                if len(vals) != 3:
                    raise Unsupported(f"{name}[{i}] length")
                out.append(("$rec", [("turns", int(vals[0])), ("permil", int(vals[1])),
                                      ("passing", bool(vals[2]))]))
            else:
                if len(vals) not in (2, 3):
                    raise Unsupported(f"{name}[{i}] length")
                out.append(("$rec", [("value", vals[0]), ("until_turn", vals[1])]
                            + ([("fresh", int(vals[2]))] if len(vals) == 3 else [])))
        return out
    return [[_num_unpack(src.v[i][j]) for j in range(src.len[i])] for i in range(src.n)]


def _pack_strintmap(pairs, dst, cap, ctx, what):
    _cap(len(pairs), cap, what)
    dst.n = len(pairs)
    for i, (k, x) in enumerate(pairs):
        if not isinstance(k, str):
            raise Unsupported(f"{what} key {k!r}")
        dst.key[i] = ctx.sid(k)
        dst.val[i] = _int(x, what)


def pack(decoded, ctx: Ctx) -> GkmsState:
    top = _rec(decoded, "top")
    if top.get("format") != "gkms.native_state.v2":
        raise Unsupported(f"format {top.get('format')!r}")
    g = GkmsState()
    g.magic, g.version, g.round_seed = MAGIC, LAYOUT_VERSION, ctx.round_seed
    ses = _rec(top["session"], "session")
    g.s_turn = _int(ses["turn"], "turn")
    g.s_finished, g.s_cleared = _bool(ses["finished"], "finished"), _bool(ses["cleared"], "cleared")
    _cap(len(ses["drinks"]), MAX_SMALL, "drinks")
    g.n_drinks = len(ses["drinks"])
    for i, d in enumerate(ses["drinks"]):
        g.drinks[i] = ctx.sid(d)
    g.rng = _int(top["rng"], "rng")
    dk = _rec(top["deck"], "deck")
    if set(dk) - set(DECK_KEYS):
        raise Unsupported(f"deck keys {sorted(set(dk) - set(DECK_KEYS))}")
    g.d_initialized = _bool(dk["initialized"], "initialized")
    g.d_current_turn = _int(dk["current_turn"], "current_turn")
    g.d_current_parameter_type = _int(dk["current_parameter_type"], "cpt")
    tpt = dk["turn_parameter_types"]
    if tpt is None:
        g.d_has_turn_parameter_types = 0
    else:
        _cap(len(tpt), MAX_TURN_TYPES, "turn_parameter_types")
        g.d_has_turn_parameter_types, g.d_n_turn_parameter_types = 1, len(tpt)
        for i, x in enumerate(tpt):
            g.d_turn_parameter_types[i] = _int(x, "tpt")
    us = dk["turn_used_support_ids"]
    if not (isinstance(us, tuple) and us[0] == "$set"):
        raise Unsupported("turn_used_support_ids")
    _cap(len(us[1]), MAX_SMALL, "turn_used_support_ids")
    g.d_n_turn_used_support_ids = len(us[1])
    for i, x in enumerate(us[1]):
        g.d_turn_used_support_ids[i] = ctx.sid(x)
    for k in ("get_card_upgrade_count", "generated_card_count", "hold_limit", "hand_limit",
              "turn_start_distribute"):
        setattr(g, "d_" + k, _int(dk[k], k))
    g.d_is_hand_hold = _bool(dk["is_hand_hold"], "is_hand_hold")
    g.d_has_grow_registry = int("dynamic_grow_by_number" in dk)
    if g.d_has_grow_registry:
        registry = dk["dynamic_grow_by_number"]
        if not isinstance(registry, tuple) or registry[0] not in ("$map", "$rec"):
            raise Unsupported("dynamic_grow_by_number")
        _cap(len(registry[1]), MAX_CARDS, "grow registry")
        g.d_n_grow_registry = len(registry[1])
        for i, (number, ids) in enumerate(registry[1]):
            entry = g.d_grow_registry[i]
            _cardno_pack(number, entry.number, ctx, "grow number")
            _cap(len(ids), MAX_GROWS, "grow registry ids")
            entry.n = len(ids)
            for j, gid in enumerate(ids): entry.ids[j] = ctx.sid(gid)
    pr = _rec(top["produce"], "produce")
    if not {"stamina", "max_stamina"} <= set(pr) or not set(pr) <= {"stamina", "max_stamina", "vocal", "dance",
                                                                      "visual", "trace"}:
        raise Unsupported("produce")
    g.p_stamina, g.p_max_stamina = _int(pr["stamina"], "p.stamina"), _int(pr["max_stamina"], "p.max")
    slot_of = {}
    zones = top["zones"]
    if len(zones) != 5:
        raise Unsupported("zones")
    n = 0
    for zi, zone in enumerate(zones):
        _cap(len(zone), MAX_ZONE, "zone")
        g.zone_n[zi] = len(zone)
        for j, c in enumerate(zone):
            _cap(n + 1, MAX_CARDS, "cards")
            rec = _rec(c, "card")
            _pack_card(rec, g.cards[n], ctx)
            slot_of[rec["guid"]] = n
            g.zone[zi][j] = n
            n += 1
    g.n_cards = n

    detached = {}

    def card_ref(v, what):
        nonlocal n
        if isinstance(v, tuple) and v[0] == "$rec":
            rec = _rec(v, what)
            key = repr(sorted(rec.items(), key=lambda kv: kv[0]))
            if key in detached:
                return detached[key]
            _cap(n + 1, MAX_CARDS, "cards")
            _pack_card(rec, g.cards[n], ctx)
            detached[key] = n
            n += 1
            g.n_cards = n
            return n - 1
        if not (isinstance(v, tuple) and v[0] == "$ref" and v[1].startswith("card|")):
            raise Unsupported(f"{what}: not a card ref {str(v)[:60]}")
        guid = v[1][5:]
        if guid not in slot_of:
            raise Unsupported(f"{what}: dangling {guid}")
        return slot_of[guid]

    g.d_has_pending_move = int("pending_move_effects" in dk)
    for item in dk.get("pending_move_effects") or ():
        rec = _rec(item, "pending move effect")
        if set(rec) != {"card", "effect_ids"}:
            raise Unsupported("pending move effect keys")
        _cap(g.d_n_pending_move + 1, MAX_PENDING_MOVE, "pending move effects")
        _cap(len(rec["effect_ids"]), 4, "pending move effect ids")
        i = g.d_n_pending_move
        g.d_pending_move_slot[i] = card_ref(rec["card"], "pending move card")
        g.d_pending_move_n[i] = len(rec["effect_ids"])
        for k, eid in enumerate(rec["effect_ids"]):
            g.d_pending_move_eff[i][k] = ctx.sid(eid)
        g.d_n_pending_move += 1

    st = _rec(top["state"], "state")
    s = g.st
    known = set()
    for spec in STATE_SCALARS:
        name, kind = spec[0], spec[1]
        known.add(name)
        if name not in st:
            if len(spec) > 2:
                continue
            raise Unsupported(f"state.{name} absent")
        v = st[name]
        if len(spec) > 2:
            setattr(s, "has_" + name, 0 if (kind in ("opt_i64", "opt_f64") and v is None) else 1)
        if kind == "i64":
            setattr(s, name, _int(v, name))
        elif kind == "bool":
            setattr(s, name, _bool(v, name))
        elif kind == "f64":
            if not isinstance(v, float):
                raise Unsupported(f"{name}: {v!r}")
            setattr(s, name, v)
        elif kind == "opt_f64":
            if v is None:
                setattr(s, name, float("nan"))
            elif isinstance(v, float):
                setattr(s, name, v)
            else:
                raise Unsupported(f"{name}: {v!r}")
        elif kind == "status":
            if v not in _STATUS_TO_CODE:
                raise Unsupported(f"{name}: {v!r}")
            setattr(s, name, _STATUS_TO_CODE[v])
        else:
            setattr(s, name, _opt(v))
    for name, value in STATE_CONSTS:
        known.add(name)
        if st.get(name, "<absent>") != value:
            raise Unsupported(f"state.{name} = {str(st.get(name, '<absent>'))[:80]} (expected {value!r})")
    known.add("_card_enchant_specs")
    s.has_card_enchant_specs = int("_card_enchant_specs" in st)
    if s.has_card_enchant_specs:
        cache = st["_card_enchant_specs"]
        if not isinstance(cache, tuple) or cache[0] not in ("$map", "$rec"):
            raise Unsupported("_card_enchant_specs")
        _cap(len(cache[1]), MAX_ENCHANTS, "_card_enchant_specs")
        s.n_card_enchant_specs = len(cache[1])
        for i, (eid, value) in enumerate(cache[1]):
            dst = s.card_enchant_specs[i]
            dst.id, dst.trigger = ctx.sid(eid), -1
            if value is None:
                continue
            spec = _rec(value, "card enchant spec")
            if set(spec) != {"trigger", "limit", "grow_ids", "enchant_id"} or spec["enchant_id"] != eid:
                raise Unsupported("card enchant spec keys/id")
            trigger = spec["trigger"]
            if not isinstance(trigger, tuple) or trigger[0] != "$ref" or not trigger[1].startswith("triggers|"):
                raise Unsupported("card enchant trigger")
            dst.trigger, dst.limit = ctx.sid(trigger[1][9:]), _int(spec["limit"], "enchant limit")
            gids = spec["grow_ids"]
            _cap(len(gids), MAX_EFFECT_IDS, "enchant grow ids")
            dst.n_grow = len(gids)
            for j, gid in enumerate(gids): dst.grows[j] = ctx.sid(gid)
    tg = _map(st["_timed_gain_layers"], "_timed_gain_layers")
    _cap(len(tg), MAX_SMALL, "_timed_gain_layers")
    s.timed_gain_layers.n = len(tg)
    for i, (name, layers) in enumerate(tg):
        s.timed_gain_layers.name[i] = ctx.sid(name)
        _cap(len(layers), MAX_TG_LAYERS, "tg layers")
        s.timed_gain_layers.nl[i] = len(layers)
        for j, lay in enumerate(layers):
            lay = _rec(lay, "tg layer")
            if set(lay) - {"fresh"} != {"expire", "value"}:
                raise Unsupported("tg layer keys")
            s.timed_gain_layers.expire[i][j] = _opt(lay["expire"])
            _num_pack(lay["value"], s.timed_gain_layers.value[i][j])
            s.timed_gain_layers.fresh[i][j] = _int(lay.get("fresh", 0), "tg fresh")
    ae = st["active_enchants"]
    _cap(len(ae), MAX_ENCHANTS, "active_enchants")
    s.n_active_enchants = len(ae)
    for i, x in enumerate(ae):
        s.active_enchants[i] = ctx.sid(x)
    debuffs = _map(st["debuff_since"], "debuff_since")
    _cap(len(debuffs), MAX_MAP, "debuff_since")
    s.debuff_since.n = len(debuffs)
    for i, (key, value) in enumerate(debuffs):
        if isinstance(key, str):
            s.debuff_since.key[i] = ctx.sid(key)
        elif isinstance(key, list) and len(key) == 2 and key[0] == "play_card_limit":
            s.debuff_since.key[i] = -ctx.sid(key[1]) - 2
        else:
            raise Unsupported(f"debuff_since key {key!r}")
        s.debuff_since.val[i] = _int(value, "debuff_since")
    ep = st["effect_play_count_buffs"]
    _cap(len(ep), MAX_SMALL, "effect_play_count_buffs")
    s.n_effect_play_count_buffs = len(ep)
    for i, b in enumerate(ep):
        b = _rec(b, "epcb")
        if set(b) != {"search_id", "repeat_count", "remaining_uses", "until_turn"}:
            raise Unsupported("epcb keys")
        d = s.effect_play_count_buffs[i]
        d.search_id = ctx.sid(b["search_id"])
        d.repeat_count, d.remaining_uses = _int(b["repeat_count"], "rc"), _int(b["remaining_uses"], "ru")
        d.until_turn = _opt(b["until_turn"])
    for qname in ("effect_timer_queue", "end_turn_timer_queue"):
        target = getattr(s, qname)
        if qname == "end_turn_timer_queue" and qname not in st:
            target.n = -1
            continue
        tq = st[qname]
        tq = [] if tq == ("$rec", []) else _map(tq, qname)
        _cap(len(tq), MAX_SMALL, qname)
        target.n = len(tq)
        for i, (turn, ids) in enumerate(tq):
            target.turn[i] = _int(turn, "timer turn")
            _cap(len(ids), MAX_TIMER_IDS, "timer ids")
            target.nids[i] = len(ids)
            for j, x in enumerate(ids):
                target.ids[i][j] = ~ctx.sid(x[1:]) if x.startswith("~") else ctx.sid(x)
    for name in STATE_TUPLE_LISTS:
        known.add(name)
        _pack_state_tuple_list(name, st.get(name, []) if name in ("review_count_add_layers", "enthusiastic_kept")
                               else st[name],
                               getattr(s, name), ctx)
    pc = st["play_card_cost_changes"]
    _cap(len(pc), MAX_SMALL, "play_card_cost_changes")
    s.n_play_card_cost_changes = len(pc)
    for i, c in enumerate(pc):
        c = _rec(c, "cost change")
        if set(c) - {"search"} != {"value", "remaining"}:
            raise Unsupported("cost change keys")
        s.play_card_cost_changes[i].value = _int(c["value"], "v")
        s.play_card_cost_changes[i].remaining = _int(c["remaining"], "r")
        s.play_card_cost_changes[i].search_id = ctx.sid(c.get("search"))
    s.playing_card = -1 if st["playing_card"] is None else card_ref(st["playing_card"], "playing_card")
    force = st["pending_force_plays"]
    _cap(len(force), MAX_CARDS, "pending_force_plays")
    s.n_force = len(force)
    for i, value in enumerate(force):
        cache = None
        if isinstance(value, tuple) and value[0] == "$rec":
            record = _rec(value, "pending_force_plays")
            s.force_has_resolved[i] = int("resolved_card" in record)
            if s.force_has_resolved[i]:
                cache = record.pop("resolved_card")
                value = ("$rec", list(record.items()))
        s.force[i] = card_ref(value, "pending_force_plays")
        if s.force_has_resolved[i]:
            from GakumasCLI.native import portable as P
            if P.encode_decoded(cache) != P.encode_decoded(_resolved_card_view(g.cards[s.force[i]], ctx)):
                raise Unsupported("pending_force_plays: stale/noncanonical resolved_card")
    installs = st["pending_trigger_installs"]
    _cap(len(installs), MAX_ENCHANTS, "pending_trigger_installs")
    s.n_pending_installs = len(installs)
    for i, value in enumerate(installs):
        s.pending_installs[i] = ctx.sid(value)
    sq = st["select_index_queue"]
    _cap(len(sq), MAX_SMALL, "select_index_queue")
    s.select_index_queue.n = len(sq)
    for i, lst in enumerate(sq):
        _cap(len(lst), MAX_SMALL, "select entry")
        s.select_index_queue.len[i] = len(lst)
        for j, x in enumerate(lst):
            s.select_index_queue.v[i][j] = _int(x, "select")
    for name in ("trigger_effect_limits", "trigger_effect_spent_totals"):
        v = st[name]
        pairs = [] if v == ("$rec", []) else _map(v, name)
        _pack_strintmap(pairs, getattr(s, name), MAX_MAP, ctx, name)
    tes = st["trigger_effect_states"]
    _cap(len(tes), MAX_TES, "trigger_effect_states")
    s.n_tes = len(tes)
    allowed = {"enchant_id", "origin", "trigger", "effect_ids", "remaining_turns", "interval44_count",
               "interval53_count", "interval52_count", "interval22_origin", "status_change_fired_turn",
               "owner_card"} | set(TES_INT) | set(TES_BOOL)
    for i, t in enumerate(tes):
        t = _rec(t, "tes")
        if set(t) - allowed:
            raise Unsupported(f"tes keys {sorted(set(t) - allowed)}")
        d = s.tes[i]
        d.enchant_id = ctx.sid(t["enchant_id"])
        d.origin = ctx.sid(t["origin"])
        tr = t["trigger"]
        if not (isinstance(tr, tuple) and tr[0] == "$ref" and tr[1].startswith("triggers|")):
            raise Unsupported(f"tes.trigger {str(tr)[:60]}")
        d.trigger = ctx.sid(tr[1][9:])
        ids = t["effect_ids"]
        _cap(len(ids), MAX_EFFECT_IDS, "tes.effect_ids")
        d.n_effect_ids = len(ids)
        for j, x in enumerate(ids):
            d.effect_ids[j] = ctx.sid(x)
        for k in TES_INT:
            setattr(d, k, _int(t[k], k))
        for k in TES_BOOL:
            setattr(d, k, _bool(t[k], k))
        d.remaining_turns = _opt(t["remaining_turns"])
        if "interval44_count" in t:
            d.has_interval44, d.interval44_count = 1, _int(t["interval44_count"], "i44")
        d.interval53_count = _int(t.get("interval53_count", 0), "i53")
        d.interval52_count = _int(t.get("interval52_count", 0), "i52")
        d.interval22_origin = _int(t.get("interval22_origin", 0), "i22o")
        d.status_change_fired_turn = _opt(t.get("status_change_fired_turn")) if "status_change_fired_turn" in t else NONE_I64
        d.owner_card = card_ref(t["owner_card"], "owner_card") if "owner_card" in t else -1
    th = st["turn_start_hand_numbers"]
    _cap(len(th), MAX_SMALL, "turn_start_hand_numbers")
    s.n_turn_start_hand_numbers = len(th)
    for i, x in enumerate(th):
        _cardno_pack(x, s.turn_start_hand_numbers[i], ctx, "turn_start_hand_numbers")
    known |= {"_timed_gain_layers", "active_enchants", "debuff_since", "effect_play_count_buffs",
              "effect_timer_queue", "end_turn_timer_queue", "play_card_cost_changes", "playing_card", "select_index_queue",
              "trigger_effect_limits", "trigger_effect_spent_totals", "trigger_effect_states",
              "turn_start_hand_numbers", "pending_force_plays", "pending_trigger_installs"}
    extra = set(st) - known
    if extra:
        raise Unsupported(f"state fields not in layout: {sorted(extra)}")
    return g


# ---------------------------------------------------------------------------
# unpack
# ---------------------------------------------------------------------------
def _unpack_card(c, ctx):
    r = {
        "number": _cardno_unpack(c.number),
        "guid": _guid_unpack(c.guid, ctx),
        "produce_card_id": ctx.s(c.produce_card_id),
        "base_upgrade_count": int(c.base_upgrade_count),
        "current_upgrade_count": int(c.current_upgrade_count),
        "temporary_upgrade_count": int(c.temporary_upgrade_count),
        "support_upgrade_ids": [ctx.s(c.support_upgrade_ids[i]) for i in range(c.n_support_upgrade_ids)],
        "play_move_position_type": int(c.play_move_position_type),
        "is_end_turn_lost": bool(c.is_end_turn_lost),
    }
    if c.has_customizes:
        r["customizes"] = [("$rec", [("id", ctx.s(c.customize_id[i])),
                                     ("customize_count", int(c.customize_count[i]))])
                           for i in range(c.n_customizes)]
    if c.has_origin_type:
        r["origin_type"] = int(c.origin_type)
    if c.has_is_initial:
        r["is_initial"] = bool(c.is_initial)
    if c.grave_source >= 0:
        r["_grave_source"] = ctx.s(c.grave_source)
    if c.play_trigger_id >= 0:
        r["playProduceExamTriggerId"] = ctx.s(c.play_trigger_id)
    if c.type9_moved >= 0:
        r["_type9_moved_from_playing"] = bool(c.type9_moved)
    if c.has_card_status_enchant:
        r["produceCardStatusEnchantId"] = ctx.s(c.card_status_enchant)
    if c.force_pays_cost >= 0:
        r["_force_play_pays_cost"] = bool(c.force_pays_cost)
    if c.has_grows:
        r["dynamic_grow_effect_ids"] = [ctx.s(c.grows[i]) for i in range(c.n_grows)]
    if c.has_enchant_fired:
        r["card_enchant_fired"] = int(c.enchant_fired)
    if c.has_stance_count_base:
        r["stance_count_base"] = int(c.stance_count_base)
    if c.has_panic_stamina_fix:
        r["panic_stamina_fix"] = int(c.panic_stamina_fix)
    if c.has_panic_stamina_until_turn:
        r["panic_stamina_until_turn"] = int(c.panic_stamina_until_turn)
    return ("$rec", list(r.items()))


def _resolved_card_view(c, ctx):
    """Rebuild the visible derived cache of a detached queued card."""
    if ctx.master is None:
        raise Unsupported("detached resolved_card requires master context")
    from GakumasCLI.engine.card_data import resolve_card_customizes
    from GakumasCLI.native import portable as P
    master = ctx.master
    cid, upgrade = ctx.s(c.produce_card_id), int(c.current_upgrade_count)
    row = master.cards.get((cid, upgrade))
    if row is None:
        tiers = [u for (k, u) in master.cards if k == cid and u <= upgrade]
        if not tiers:
            raise Unsupported(f"detached card row: {cid}/{upgrade}")
        row = master.cards[cid, max(tiers)]
    instance = {
        "customizes": [{"id": ctx.s(c.customize_id[i]), "customize_count": int(c.customize_count[i])}
                       for i in range(c.n_customizes)] if c.has_customizes else [],
        "dynamic_grow_effect_ids": [ctx.s(c.grows[i]) for i in range(c.n_grows)] if c.has_grows else [],
    }
    resolved = resolve_card_customizes(instance, row, master.customizes, master.grow_effects)
    data = bytearray()
    P.Encoder(P.master_refmap(master)).enc(resolved, data)
    return P.decode(bytes(data))[0]


def synchro_state(g: GkmsState):
    """the state holds シンクロ data the portable record has no field for (bundles, removed cards, fusion limits,
    metamorphose, a group an effect set).
    """
    return bool(g.zone_n[5] or g.zone_n[6] or g.st.meta_stage or any(g.st.fusion_delta[k] for k in range(4))
                or any(g.cards[i].fusion_top1 or g.cards[i].p4_group for i in range(g.n_cards)))


def check_portable(g: GkmsState):
    if synchro_state(g):
        raise Unsupported("synchro state (no portable form)")


def unpack(g: GkmsState, ctx: Ctx):
    if g.magic != MAGIC or g.version != LAYOUT_VERSION:
        raise ValueError("bad magic/version")
    guid_of = [_guid_unpack(g.cards[i].guid, ctx) for i in range(g.n_cards)]
    zoned = {g.zone[zi][j] for zi in range(5) for j in range(g.zone_n[zi])}

    def ref(slot, include_resolved=False):
        if slot not in zoned:
            value = _unpack_card(g.cards[slot], ctx)
            if include_resolved:
                value[1].append(("resolved_card", _resolved_card_view(g.cards[slot], ctx)))
            return value
        return ("$ref", "card|" + guid_of[slot])

    zones = [[_unpack_card(g.cards[g.zone[zi][j]], ctx) for j in range(g.zone_n[zi])] for zi in range(5)]
    session = ("$rec", [("turn", int(g.s_turn)), ("step_index", None), ("finished", bool(g.s_finished)),
                        ("cleared", bool(g.s_cleared)),
                        ("drinks", [ctx.s(g.drinks[i]) for i in range(g.n_drinks)])])
    deck = ("$rec", [
        ("initialized", bool(g.d_initialized)), ("current_turn", int(g.d_current_turn)),
        ("current_parameter_type", int(g.d_current_parameter_type)),
        ("turn_parameter_types", [int(g.d_turn_parameter_types[i]) for i in range(g.d_n_turn_parameter_types)]
         if g.d_has_turn_parameter_types else None),
        ("turn_used_support_ids", ("$set", [ctx.s(g.d_turn_used_support_ids[i])
                                            for i in range(g.d_n_turn_used_support_ids)])),
        ("get_card_upgrade_count", int(g.d_get_card_upgrade_count)),
        ("generated_card_count", int(g.d_generated_card_count)),
        ("is_hand_hold", bool(g.d_is_hand_hold)), ("hold_limit", int(g.d_hold_limit)),
        ("hand_limit", int(g.d_hand_limit)), ("turn_start_distribute", int(g.d_turn_start_distribute)),
    ])
    pending_move = [("$rec", [("card", ref(int(g.d_pending_move_slot[i]))),
                              ("effect_ids", [ctx.s(g.d_pending_move_eff[i][k]) for k in range(g.d_pending_move_n[i])])])
                    for i in range(g.d_n_pending_move)]
    if g.d_has_grow_registry:
        entries = [(_cardno_unpack(g.d_grow_registry[i].number),
                    [ctx.s(g.d_grow_registry[i].ids[j]) for j in range(g.d_grow_registry[i].n)])
                   for i in range(g.d_n_grow_registry)]
        kind = "$rec" if all(isinstance(k, str) and k.isidentifier() for k, _ in entries) else "$map"
        deck[1].append(("dynamic_grow_by_number", (kind, entries)))
    if g.d_has_pending_move:
        deck[1].append(("pending_move_effects", pending_move))
    produce = ("$rec", [("stamina", int(g.p_stamina)), ("max_stamina", int(g.p_max_stamina)), ("trace", [])])
    s = g.st
    st = {}
    for spec in STATE_SCALARS:
        name, kind = spec[0], spec[1]
        if len(spec) > 2 and not getattr(s, "has_" + name):
            if spec[1] in ("opt_i64", "opt_f64"):
                st[name] = None
            else:
                continue
        v = getattr(s, name)
        if kind == "i64":
            st[name] = int(v)
        elif kind == "bool":
            st[name] = bool(v)
        elif kind == "f64":
            st[name] = float(v)
        elif kind == "opt_f64":
            if getattr(s, "has_" + name) and not math.isnan(float(v)):
                st[name] = float(v)
            else:
                st[name] = None
        elif kind == "status":
            try:
                st[name] = _CODE_TO_STATUS[int(v)]
            except KeyError:
                raise Unsupported(f"{name}: {v}")
        else:
            st[name] = _unopt(v)
    for name, value in STATE_CONSTS:
        st[name] = value
    if s.has_card_enchant_specs:
        cache = []
        for i in range(s.n_card_enchant_specs):
            spec = s.card_enchant_specs[i]
            eid = ctx.s(spec.id)
            value = None if spec.trigger < 0 else ("$rec", [
                ("trigger", ("$ref", "triggers|" + ctx.s(spec.trigger))),
                ("limit", int(spec.limit)),
                ("grow_ids", [ctx.s(spec.grows[j]) for j in range(spec.n_grow)]),
                ("enchant_id", eid)])
            cache.append((eid, value))
        st["_card_enchant_specs"] = ("$map" if cache else "$rec", cache)
    tg = s.timed_gain_layers
    st["_timed_gain_layers"] = ("$map", [
        (ctx.s(tg.name[i]), [("$rec", [("expire", _unopt(tg.expire[i][j])), ("value", _num_unpack(tg.value[i][j])),
                                       ("fresh", int(tg.fresh[i][j]))])
                             for j in range(tg.nl[i])])
        for i in range(tg.n)])
    st["active_enchants"] = [ctx.s(s.active_enchants[i]) for i in range(s.n_active_enchants)]
    ds = s.debuff_since
    st["debuff_since"] = ("$map", [(ctx.s(ds.key[i]) if ds.key[i] >= 0 else ["play_card_limit", ctx.s(-ds.key[i] - 2)], int(ds.val[i])) for i in range(ds.n)])
    st["effect_play_count_buffs"] = [
        ("$rec", [("search_id", ctx.s(b.search_id)), ("repeat_count", int(b.repeat_count)),
                  ("remaining_uses", int(b.remaining_uses)), ("until_turn", _unopt(b.until_turn))])
        for b in s.effect_play_count_buffs[: s.n_effect_play_count_buffs]]
    for qname in ("effect_timer_queue", "end_turn_timer_queue"):
        tq = getattr(s, qname)
        if tq.n < 0:
            continue
        st[qname] = (("$map", [(int(tq.turn[i]), [_timer_id(ctx, tq.ids[i][j]) for j in range(tq.nids[i])])
                               for i in range(tq.n)]) if tq.n else ("$rec", []))
    for name in STATE_TUPLE_LISTS:
        tl = getattr(s, name)
        st[name] = _unpack_state_tuple_list(name, tl, ctx)
    st["play_card_cost_changes"] = [("$rec", [("value", int(c.value)), ("remaining", int(c.remaining)),
                                              ("search", ctx.s(c.search_id))])
                                    for c in s.play_card_cost_changes[: s.n_play_card_cost_changes]]
    st["playing_card"] = None if s.playing_card < 0 else ref(s.playing_card)
    st["pending_force_plays"] = [ref(s.force[i], bool(s.force_has_resolved[i])) for i in range(s.n_force)]
    st["pending_trigger_installs"] = [ctx.s(s.pending_installs[i]) for i in range(s.n_pending_installs)]
    sq = s.select_index_queue
    st["select_index_queue"] = [[int(sq.v[i][j]) for j in range(sq.len[i])] for i in range(sq.n)]
    for name in ("trigger_effect_limits", "trigger_effect_spent_totals"):
        m = getattr(s, name)
        st[name] = (("$map", [(ctx.s(m.key[i]), int(m.val[i])) for i in range(m.n)]) if m.n else ("$rec", []))
    tes = []
    for i in range(s.n_tes):
        t = s.tes[i]
        r = [("enchant_id", ctx.s(t.enchant_id)), ("origin", ctx.s(t.origin)),
             ("trigger", ("$ref", "triggers|" + ctx.s(t.trigger))),
             ("effect_ids", [ctx.s(t.effect_ids[j]) for j in range(t.n_effect_ids)]),
             ("remaining_turns", _unopt(t.remaining_turns))]
        r += [(k, int(getattr(t, k))) for k in TES_INT]
        r += [(k, bool(getattr(t, k))) for k in TES_BOOL]
        if t.has_interval44:
            r.append(("interval44_count", int(t.interval44_count)))
        if t.interval53_count:
            r.append(("interval53_count", int(t.interval53_count)))
        if t.interval52_count:
            r.append(("interval52_count", int(t.interval52_count)))
        if t.interval22_origin:
            r.append(("interval22_origin", int(t.interval22_origin)))
        if t.status_change_fired_turn != NONE_I64:
            r.append(("status_change_fired_turn", int(t.status_change_fired_turn)))
        if t.owner_card >= 0:
            r.append(("owner_card", ref(t.owner_card)))
        tes.append(("$rec", r))
    st["trigger_effect_states"] = tes
    st["turn_start_hand_numbers"] = [_cardno_unpack(s.turn_start_hand_numbers[i])
                                     for i in range(s.n_turn_start_hand_numbers)]
    return ("$rec", [("format", "gkms.native_state.v2"), ("session", session), ("rng", int(g.rng)),
                     ("zones", zones), ("deck", deck), ("produce", produce), ("state", ("$rec", list(st.items())))])


# ---------------------------------------------------------------------------
# C header
# ---------------------------------------------------------------------------
_CT = {C.c_int64: "int64_t", C.c_int32: "int32_t", C.c_int16: "int16_t", C.c_int8: "int8_t",
       C.c_uint8: "uint8_t", C.c_uint32: "uint32_t", C.c_double: "double"}


def _ctype_decl(t, name):
    dims = []
    while hasattr(t, "_length_"):
        dims.append(t._length_)
        t = t._type_
    base = _CT.get(t) or t.__name__
    return f"{base} {name}" + "".join(f"[{d}]" for d in dims)


def gen_header():
    out = ["/* generated by GakumasCLI/native/layout.py -- do not edit */",
           "#pragma once", "#ifndef __CUDACC_RTC__", "#include <stdint.h>", "#endif", "",
           f"#define GKMS_LAYOUT_VERSION {LAYOUT_VERSION}", f"#define GKMS_MAGIC 0x{MAGIC:08X}u",
           "#define GKMS_NONE_I64 INT64_MIN"]
    for k, v in globals().items():
        if k.startswith("MAX_") and isinstance(v, int):
            out.append(f"#define GKMS_{k} {v}")
    out.append("")
    for cls in (Num, CardNo, GrowRegistry, Card, CardEnchantSpec, TES, TupleList, StrIntMap, SmallStrIntMap, TimedGain, PlayCountBuff,
                CostChange, TimerQueue, SelectQueue, ExamStateP, GkmsState):
        out.append(f"typedef struct {cls.__name__} {{")
        for fname, ftype in cls._fields_:
            out.append(f"    {_ctype_decl(ftype, fname)};")
        out.append(f"}} {cls.__name__};")
        out.append(f"/* sizeof {C.sizeof(cls)} */")
        out.append("")
    offs = [(n, getattr(GkmsState, n).offset) for n, _ in GkmsState._fields_]
    out.append(f"#define GKMS_STATE_SIZE {C.sizeof(GkmsState)}")
    out.append("/* offsets: " + ", ".join(f"{n}={o}" for n, o in offs) + " */")
    return "\n".join(out) + "\n"
