# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""master + round compiler for the native core."""
from __future__ import annotations

import ctypes as C

I32, I64, U8 = C.c_int32, C.c_int64, C.c_uint8


class EffectRow(C.Structure):
    _fields_ = [("id", I32), ("type", I32), ("v1", I64), ("has_v1", I32), ("v2", I64), ("has_v2", I32),
                ("count", I64), ("has_count", I32), ("turn", I64), ("has_turn", I32),
                ("search", I32), ("enchant", I32), ("target_card", I32), ("chain", I32),
                ("pick_range_type", I32), ("pick_min", I64), ("has_pick_min", I32),
                ("pick_max", I64), ("has_pick_max", I32), ("pick_count_type", I32), ("pick_ref", I32),
                ("move_pos", I32), ("grow_off", I32), ("n_grow", I32),
                ("chain2", I32)]


class TriggerRow(C.Structure):
    _fields_ = [("id", I32), ("phase_off", I32), ("n_phase", I32), ("pval_off", I32), ("n_pval", I32),
                ("fst_off", I32), ("n_fst", I32), ("fsv_off", I32), ("n_fsv", I32),
                ("fsc_off", I32), ("n_fsc", I32), ("etype_off", I32), ("n_etype", I32),
                ("lesson_type", I32), ("search", I32),
                ("fss_off", I32), ("n_fss", I32)]


class EnchantRow(C.Structure):
    _fields_ = [("id", I32), ("trigger", I32), ("eff_off", I32), ("n_eff", I32)]


class SearchRow(C.Structure):
    _fields_ = [("id", I32), ("cat_off", I32), ("n_cat", I32), ("pos", I32), ("rar_off", I32), ("n_rar", I32),
                ("tag", I32), ("eg_off", I32), ("n_eg", I32), ("is_self", I32), ("limit", I64), ("has_limit", I32),
                ("order_type", I32), ("plan", I32), ("card_off", I32), ("n_card", I32), ("pool", I32),
                ("stmin", I64), ("has_stmin", I32), ("stmax", I64), ("has_stmax", I32),
                ("upg_off", I32), ("n_upg", I32),
                ("p4_mask", I32), ("fusion_min", I64), ("has_fusion_min", I32), ("fusion_max", I64),
                ("has_fusion_max", I32), ("fusion_top_only", I32), ("pick_fusion", I32), ("sy", I32)]


class LinkRow(C.Structure):
    _fields_ = [("effect", I32), ("trigger", I32), ("once", I32), ("v1", I64)]


class CardRow(C.Structure):
    _fields_ = [("id", I32), ("upg", I32), ("category", I32), ("rarity", I32), ("plan", I32),
                ("cost_type", I32), ("cost_value", I64), ("has_cost_value", I32),
                ("stamina", I64), ("has_stamina", I32), ("force", I64), ("has_force", I32),
                ("eg_off", I32), ("n_eg", I32), ("tag", I32), ("link_off", I32), ("n_link", I32),
                ("move_pos", I32), ("play_trigger", I32), ("status_enchant", I32), ("is_initial", I32),
                ("move_trigger", I32), ("mv_off", I32), ("n_mv", I32),
                ("p4_group", I32)]


class CustRow(C.Structure):
    _fields_ = [("id", I32), ("count", I32), ("grow_off", I32), ("n_grow", I32), ("card", I32), ("upg", I32),
                ("idx", I32)]


class GrowRow(C.Structure):
    _fields_ = [("id", I32), ("type", I32), ("value", I64), ("play_effect", I32), ("tgt_off", I32), ("n_tgt", I32),
                ("move_pos", I32), ("status_enchant", I32), ("ttgt_off", I32), ("n_ttgt", I32),
                ("play_trigger", I32), ("play_effect_trigger", I32), ("eg_off", I32), ("n_eg", I32)]


class PoolRow(C.Structure):
    _fields_ = [("id", I32), ("ent_off", I32), ("n_ent", I32)]


class DrinkRow(C.Structure):
    _fields_ = [("id", I32), ("eff_off", I32), ("n_eff", I32)]


class Support(C.Structure):
    _fields_ = [("row", I32), ("permil", I64), ("parameter_type", I32), ("csp", I32)]


class GimmickRow(C.Structure):
    """One data row from ProduceExamGimmickEffectGroup."""
    _fields_ = [
        ("start_turn", I32), ("priority", I32),
        ("field_status_type", I32),
        ("field_status_search", I32),
        ("field_status_value", I64),
        ("field_status_check_type", I32), ("is_positive", I32),
        ("effect", I32), ("pre_draw", I32),
    ]


class CardEnchantRow(C.Structure):
    _fields_ = [("id", I32), ("trigger", I32), ("limit", I64),
                ("grow_off", I32), ("n_grow", I32), ("stance", I32)]


def card_enchant_spec(enchant_id, triggers, rows=None):
    if not enchant_id.startswith("card_enchant-"):
        return None
    row = (rows or {}).get(enchant_id)
    if row is not None:
        trigger_id = row.get("produceExamTriggerId")
        limit = int(row.get("triggerCount") or -1)
        grows = list(row.get("produceCardGrowEffectIds") or ())
    else:
        body = enchant_id[len("card_enchant-"):]
        split = body.find("-g_effect-")
        head, rest = (body, "") if split < 0 else (body[:split], body[split + 1:])
        trigger_id, limit = head, -1
        if head not in triggers:
            base, _, tail = head.rpartition("-")
            if tail.isdigit() and base in triggers: trigger_id, limit = base, int(tail)
        grows = ["g_effect-" + part.rstrip("-") for part in rest.split("g_effect-") if part]
    if trigger_id not in triggers or not grows: return None
    stance = (3 if "stance_change_full_power" in trigger_id else
              1 if "stance_change_concentration" in trigger_id else
              2 if "stance_change_preservation" in trigger_id else 0)
    trigger = triggers[trigger_id]
    if not stance and 13 in [r["number"] for r in trigger.get("phaseTypes", ())]:
        stance = -int(((trigger.get("effectTypes") or [{}])[0].get("number")) or 0)
    return trigger_id, limit, grows, stance


class MasterDesc(C.Structure):
    _fields_ = [
        ("n_strings", I32),
        ("card_enchants", C.POINTER(CardEnchantRow)), ("n_card_enchants", I32),
        ("sid_leisurely_grow", I32),
        ("effects", C.POINTER(EffectRow)), ("n_effects", I32),
        ("triggers", C.POINTER(TriggerRow)), ("n_triggers", I32),
        ("enchants", C.POINTER(EnchantRow)), ("n_enchants", I32),
        ("searches", C.POINTER(SearchRow)), ("n_searches", I32),
        ("cards", C.POINTER(CardRow)), ("n_cards", I32),
        ("links", C.POINTER(LinkRow)), ("n_links", I32),
        ("custs", C.POINTER(CustRow)), ("n_custs", I32),
        ("grows", C.POINTER(GrowRow)), ("n_grows", I32),
        ("pools", C.POINTER(PoolRow)), ("n_pools", I32),
        ("drinks", C.POINTER(DrinkRow)), ("n_drinks", I32),
        ("pool32", C.POINTER(I32)), ("n_pool32", I32),
        ("pool64", C.POINTER(I64)), ("n_pool64", I32),
        ("by_sid", C.POINTER(I32)),
        ("es_param_buff_permil", I64), ("es_param_buff_multiple_per_turn_permil", I64),
        ("es_sc_add_permil", I64), ("es_sc_add_down_permil", I64), ("es_sc_down_permil", I64),
        ("es_sc_down_add_permil", I64), ("es_turn_end_recovery", I64), ("es_gimmick_param_debuff_permil", I64),
        ("es_block_add_down_permil", I64),
        ("es_conc_penetrate_reduce2", I64), ("es_conc_lesson1", I64), ("es_conc_lesson2", I64),
        ("es_pres_lesson1", I64), ("es_pres_lesson2", I64), ("es_fp_lesson", I64),
        ("es_conc_stamina1", I64), ("es_conc_stamina2", I64),
        ("es_pres_stamina1", I64), ("es_pres_stamina2", I64),
        ("es_stamina_reduce_change", I64), ("es_conc_stamina_penetrate_reduce2", I64),
        ("es_lpm_depend_permil", I64), ("es_lpm_depend_max_permil", I64),
        ("es_over_pres_block_add", I64),
        ("es_pres_release_enth1", I64), ("es_pres_release_enth2", I64), ("es_pres_release_block2", I64),
        ("es_pres_release_playable1", I64), ("es_pres_release_playable2", I64),
        ("es_over_pres_release_enth", I64), ("es_over_pres_release_playable", I64), ("es_fp_playable_add", I64),
        ("es_buff_add_permil", I64),
        ("es_fusion_limit", I64 * 4), ("es_meta", I64 * 5),
        ("panic_candidates_off", I32), ("n_panic_candidates", I32),
        ("setting_score_threshold_multiple", I64),
        ("sid_empty", I32), ("sid_drink", I32), ("sid_memory", I32), ("sid_post_play", I32), ("sid_reset_hand", I32),
        ("sid_trg_trouble2", I32), ("sid_trg_lb6", I32), ("sid_trg_pb4", I32),
        ("sid_tg", I32 * 8),
        ("sid_debuff_kind", I32 * 220),
        ("es_fp_reduce_cancel_disable", I64),
    ]


class RoundDesc(C.Structure):
    _fields_ = [
        ("limit_turn", I64), ("final_audition", I32), ("has_score_cap", I32), ("score_cap", I64),
        ("permil", I64 * 4), ("has_permil", I32 * 4),
        ("n_supports", I32), ("supports", Support * 8),
        ("tier_mask", C.POINTER(U8)),
        ("play_move_position_type_enabled", I32),
        ("n_gimmick_rows", I32), ("gimmick_rows", C.POINTER(GimmickRow)),
        ("n_idol_enchants", I32), ("idol_enchants", C.POINTER(I32)),
        ("select_greedy", I32),
        ("hand_limit_const", I32),
        ("skip_clears_adds", I32),
        ("n_research_cards", I32), ("research_cards", C.POINTER(I32)),
        ("has_cfg_pt", I32), ("n_cfg_turn_pt", I32), ("cfg_pt", I64), ("cfg_turn_pt", I64 * 40),
        ("synchro", I32),
    ]


TIMED_GAIN_NAMES = ("lesson_buff_additive_multiple", "parameter_buff_additive_multiple",
                    "aggressive_additive_multiple", "review_additive_multiple",
                    "lesson_buff_additive_fix", "aggressive_additive_fix",
                    "enthusiastic_multiple_permil",
                    "parameter_buff_additive_fix")
DEBUFF_KIND = {18: "block_restriction", 69: "stamina_consumption_add", 73: "block_add_down",
               84: "stamina_consumption_add_fix", 86: "stamina_recover_restriction", 105: "lesson_debuff",
               106: "gimmick_parameter_debuff", 107: "block_add_down_fix", 114: "slump",
               115: "card_draw_down", 123: "lesson_change_specify_more_than", 141: "stance_lock",
               155: "lesson_parameter_multiple_down", 78: "panic",
               199: "buff_consumption_add"}
FIXED_STRINGS = ("", "drink", "memory", "post_play", "reset_hand",
                 "e_trigger-none-card_search_count_up-2-p_card_search-trouble-not_lost",
                 "e_trigger-exam_card_play-lesson_buff_up-6",
                 "e_trigger-exam_card_play-parameter_buff_up-4") + TIMED_GAIN_NAMES + tuple(DEBUFF_KIND.values()) \
    + tuple(f"realign-{k}" for k in range(4))


def _num(v):
    if isinstance(v, dict):
        return int(v["number"])
    return None if v is None else int(v)


class Compiler:
    """Two uses: ``strings_needed(master)`` (pass 1) and ``compile(master, strings)``."""

    def __init__(self, strings):
        self.strings = strings
        self.index = {s: i for i, s in enumerate(strings)}
        self.p32 = []
        self.p64 = []

    def sid(self, s):
        if s is None:
            return -1
        return self.index[s]

    def l32(self, xs):
        off = len(self.p32)
        self.p32.extend(xs)
        return off, len(xs)

    def l64(self, xs):
        off = len(self.p64)
        self.p64.extend(int(x) for x in xs)
        return off, len(xs)


def _strings_of_row(table, row, out):
    """The string-valued fields the compiler interns for one master row."""
    def add(v):
        if isinstance(v, str):
            out.add(v)
    if table == "effects":
        for k in ("id", "produceCardSearchId", "produceExamStatusEnchantId", "targetProduceCardId",
                  "chainProduceExamEffectId", "pickCountReferenceProduceCardSearchId"):
            add(row.get(k))
        for gid in row.get("produceCardGrowEffectIds") or (): add(gid)
    elif table == "triggers":
        add(row.get("id")); add(row.get("produceCardSearchId"))
    elif table == "enchants":
        add(row.get("id")); add(row.get("produceExamTriggerId"))
        for x in row.get("produceExamEffectIds") or ():
            add(x)
    elif table == "searches":
        add(row.get("id")); add(row.get("cardSearchTag")); add(row.get("produceCardPoolId"))
        for x in list(row.get("effectGroupIds") or ()) + list(row.get("produceCardIds") or ()):
            add(x)
    elif table == "cards":
        for k in ("id", "searchTag", "playProduceExamTriggerId", "produceCardStatusEnchantId"):
            add(row.get(k))
        for x in row.get("effectGroupIds") or ():
            add(x)
        for link in row.get("playEffects") or ():
            add(link.get("produceExamEffectId")); add(link.get("produceExamTriggerId"))
    elif table == "customizes":
        add(row.get("id"))
        for x in row.get("produceCardGrowEffectIds") or ():
            add(x)
    elif table == "grow_effects":
        for k in ("id", "playProduceExamEffectId", "produceCardStatusEnchantId", "playProduceExamTriggerId",
                  "playEffectProduceExamTriggerId"):
            add(row.get(k))
        for x in list(row.get("targetPlayProduceExamEffectIds") or ()) + \
                list(row.get("targetPlayEffectProduceExamTriggerIds") or ()) + list(row.get("effectGroupIds") or ()):
            add(x)
    elif table == "card_pools":
        add(row.get("id"))
        for r in row.get("produceCardRatios") or ():
            add(r.get("id"))
    elif table == "drinks":
        add(row.get("id"))


TABLES = ("effects", "triggers", "enchants", "searches", "cards", "customizes", "grow_effects",
          "card_pools", "drinks")


def strings_needed(master):
    out = set(FIXED_STRINGS)
    for t in TABLES:
        for k, row in (getattr(master, t, None) or {}).items():
            out.add(k[0] if isinstance(k, tuple) else k)
            _strings_of_row(t, row, out)
    for k, row in (master.drink_effects or {}).items():
        out.add(row.get("produceExamEffectId"))
    out.update(master.idol_unique_item_enchant_ids())
    out.discard(None)
    amount = int((master.exam_setting or {}).get("overPreservationReleaseToFullPowerGrowEffectLessonAdd", 10) or 0)
    out.add(f"g_effect-lesson_add-{amount}")
    for sid in list(out):
        spec = card_enchant_spec(sid, master.triggers, getattr(master, "card_status_enchants", None))
        if spec: out.update(spec[2])
    return out


def compile_master(master, strings):
    cp = Compiler(strings)
    n = len(strings)
    by = (I32 * (8 * n))(*([-1] * (8 * n)))

    def opt_i(row, k):
        v = row.get(k)
        return (0, 0) if v is None else (int(v), 1)

    effects = []
    for k, e in master.effects.items():
        r = EffectRow()
        r.id = cp.sid(k)
        r.type = _num(e.get("effectType")) or 0
        r.v1, r.has_v1 = opt_i(e, "effectValue1")
        if r.type == 6 and e.get("targetUpgradeCount") is not None:
            r.v1, r.has_v1 = int(e["targetUpgradeCount"]), 1
        r.v2, r.has_v2 = opt_i(e, "effectValue2")
        r.count, r.has_count = opt_i(e, "effectCount")
        r.turn, r.has_turn = opt_i(e, "effectTurn")
        r.search = cp.sid(e.get("produceCardSearchId"))
        r.enchant = cp.sid(e.get("produceExamStatusEnchantId"))
        r.target_card = cp.sid(e.get("targetProduceCardId"))
        r.chain = cp.sid(e.get("chainProduceExamEffectId"))
        r.chain2 = -1
        chain_ids = list(e.get("chainProduceExamEffectIds") or ())
        if chain_ids:
            if len(chain_ids) <= 2:
                r.chain = cp.sid(chain_ids[0])
                r.chain2 = cp.sid(chain_ids[1]) if len(chain_ids) == 2 else -1
            else:
                r.chain = -2
        r.pick_range_type = _num(e.get("pickRangeType")) if e.get("pickRangeType") is not None else -1
        r.pick_min, r.has_pick_min = opt_i(e, "pickCountMin")
        r.pick_max, r.has_pick_max = opt_i(e, "pickCountMax")
        r.pick_count_type = _num(e.get("pickCountType")) if e.get("pickCountType") is not None else -1
        r.pick_ref = cp.sid(e.get("pickCountReferenceProduceCardSearchId"))
        r.move_pos = _num(e.get("movePositionType")) if e.get("movePositionType") is not None else -1
        r.grow_off, r.n_grow = cp.l32([cp.sid(x) for x in e.get("produceCardGrowEffectIds") or ()])
        by[0 * n + r.id] = len(effects)
        effects.append(r)

    triggers = []
    for k, t in master.triggers.items():
        r = TriggerRow()
        r.id = cp.sid(k)
        r.phase_off, r.n_phase = cp.l64([x["number"] for x in t.get("phaseTypes") or ()])
        r.pval_off, r.n_pval = cp.l64([int(x) for x in t.get("phaseValues") or ()])
        r.fst_off, r.n_fst = cp.l64([x["number"] for x in t.get("fieldStatusTypes") or ()])
        r.fsv_off, r.n_fsv = cp.l64([int(x) for x in t.get("fieldStatusValues") or ()])
        r.fsc_off, r.n_fsc = cp.l64([int((x or {}).get("number", 0)) for x in t.get("fieldStatusCheckTypes") or ()])
        r.etype_off, r.n_etype = cp.l64([x["number"] for x in t.get("effectTypes") or ()])
        r.lesson_type = _num(t.get("lessonType")) if t.get("lessonType") is not None else -1
        r.search = cp.sid(t.get("produceCardSearchId"))
        r.fss_off, r.n_fss = cp.l32([cp.sid(x) for x in t.get("fieldStatusProduceCardSearchIds") or ()])
        by[1 * n + r.id] = len(triggers)
        triggers.append(r)

    enchants = []
    for k, e in master.enchants.items():
        r = EnchantRow()
        r.id = cp.sid(k)
        r.trigger = cp.sid(e.get("produceExamTriggerId"))
        r.eff_off, r.n_eff = cp.l32([cp.sid(x) for x in e.get("produceExamEffectIds") or ()])
        by[2 * n + r.id] = len(enchants)
        enchants.append(r)

    searches = []
    for k, s in master.searches.items():
        r = SearchRow()
        r.id = cp.sid(k)
        r.cat_off, r.n_cat = cp.l64([_num(x) for x in s.get("cardCategories") or ()])
        r.pos = _num(s.get("cardPositionType")) if s.get("cardPositionType") is not None else -1
        r.rar_off, r.n_rar = cp.l64([_num(x) for x in s.get("cardRarities") or ()])
        r.tag = cp.sid(s.get("cardSearchTag"))
        r.eg_off, r.n_eg = cp.l32([cp.sid(x) for x in s.get("effectGroupIds") or ()])
        r.is_self = int(bool(s.get("isSelf")))
        r.limit, r.has_limit = opt_i(s, "limitCount")
        r.order_type = _num(s.get("orderType")) if s.get("orderType") is not None else -1
        r.plan = _num(s.get("planType")) if s.get("planType") is not None else -1
        r.card_off, r.n_card = cp.l32([cp.sid(x) for x in s.get("produceCardIds") or ()])
        r.pool = cp.sid(s.get("produceCardPoolId"))
        r.stmin, r.has_stmin = opt_i(s, "staminaMin")
        r.stmax, r.has_stmax = opt_i(s, "staminaMax")
        r.upg_off, r.n_upg = cp.l64([int(x) for x in s.get("upgradeCounts") or ()])
        r.p4_mask = sum(1 << _num(x) for x in s.get("plan4GroupTypes") or () if 0 < (_num(x) or 0) < 31)
        r.fusion_min, r.has_fusion_min = opt_i(s, "fusionCountMin")
        r.fusion_max, r.has_fusion_max = opt_i(s, "fusionCountMax")
        r.fusion_top_only = int(bool(s.get("isFusionTopOnly")))
        r.pick_fusion = _num(s.get("produceCardPickFusionType")) or 0 if s.get("produceCardPickFusionType") else 0
        r.sy = int(bool(r.p4_mask or r.has_fusion_min or r.has_fusion_max or r.fusion_top_only or r.pick_fusion))
        by[3 * n + r.id] = len(searches)
        searches.append(r)

    links = []
    cards = []
    for key in sorted(master.cards, key=lambda kv: (cp.sid(kv[0]), kv[1])):
        c = master.cards[key]
        r = CardRow()
        r.id, r.upg = cp.sid(key[0]), int(key[1])
        r.category = _num(c.get("category")) or 0
        r.rarity = _num(c.get("rarity")) or 0
        r.plan = _num(c.get("planType")) or 0
        r.cost_type = _num(c.get("costType")) or 0 if c.get("costType") is not None else 0
        r.cost_value, r.has_cost_value = opt_i(c, "costValue")
        r.stamina, r.has_stamina = opt_i(c, "stamina")
        r.force, r.has_force = opt_i(c, "forceStamina")
        r.eg_off, r.n_eg = cp.l32([cp.sid(x) for x in c.get("effectGroupIds") or ()])
        r.tag = cp.sid(c.get("searchTag"))
        r.link_off = len(links)
        for link in c.get("playEffects") or ():
            L = LinkRow()
            L.effect = cp.sid(link.get("produceExamEffectId"))
            L.trigger = cp.sid(link.get("produceExamTriggerId")) if "produceExamTriggerId" in link else -1
            L.once = int(bool(link.get("isOncePlayEffect")))
            L.v1 = int(link.get("effectValue1") or 0)
            links.append(L)
        r.n_link = len(links) - r.link_off
        r.move_pos = _num(c.get("playMovePositionType")) if c.get("playMovePositionType") is not None else -1
        r.play_trigger = cp.sid(c.get("playProduceExamTriggerId")) if "playProduceExamTriggerId" in c else -1
        r.status_enchant = cp.sid(c.get("produceCardStatusEnchantId")) if "produceCardStatusEnchantId" in c else -1
        r.is_initial = int(bool(c.get("isInitial")))
        r.move_trigger = _num(c.get("moveEffectTriggerType")) if c.get("moveEffectTriggerType") is not None else -1
        r.mv_off, r.n_mv = cp.l32([cp.sid(x) for x in c.get("moveProduceExamEffectIds") or ()])
        r.p4_group = _num(c.get("plan4GroupType")) or 0 if c.get("plan4GroupType") else 0
        if by[7 * n + r.id] < 0:
            by[7 * n + r.id] = len(cards)
        cards.append(r)

    tiers = {}
    for key, c in master.cards.items():
        for i, x in enumerate(c.get("produceCardCustomizeIds") or ()):
            tiers.setdefault(x, []).append((key[0], int(key[1]), i))
    custs = []
    for key in sorted(master.customizes, key=lambda kv: (cp.sid(kv[0]), kv[1])):
        row = master.customizes[key]
        r = CustRow()
        r.id, r.count = cp.sid(key[0]), int(key[1])
        one = tiers.get(key[0], ())
        r.card, r.upg, r.idx = (cp.sid(one[0][0]), one[0][1], one[0][2]) if len(one) == 1 else (-1, -1, -1)
        r.grow_off, r.n_grow = cp.l32([cp.sid(x) for x in row.get("produceCardGrowEffectIds") or ()])
        custs.append(r)

    grows = []
    for k, g in master.grow_effects.items():
        r = GrowRow()
        r.id = cp.sid(k)
        r.type = _num(g.get("effectType")) or 0
        r.value = int(g.get("value") or 0)
        r.play_effect = cp.sid(g.get("playProduceExamEffectId"))
        r.tgt_off, r.n_tgt = cp.l32([cp.sid(x) for x in g.get("targetPlayProduceExamEffectIds") or ()])
        r.move_pos = _num(g.get("playMovePositionType")) if g.get("playMovePositionType") is not None else -1
        r.status_enchant = cp.sid(g.get("produceCardStatusEnchantId"))
        r.ttgt_off, r.n_ttgt = cp.l32([cp.sid(x) for x in g.get("targetPlayEffectProduceExamTriggerIds") or ()])
        r.play_trigger = cp.sid(g.get("playProduceExamTriggerId"))
        r.play_effect_trigger = cp.sid(g.get("playEffectProduceExamTriggerId"))
        r.eg_off, r.n_eg = cp.l32([cp.sid(x) for x in g.get("effectGroupIds") or ()])
        by[6 * n + r.id] = len(grows)
        grows.append(r)

    pools = []
    for k, p in master.card_pools.items():
        r = PoolRow()
        r.id = cp.sid(k)
        ents = []
        for e in p.get("produceCardRatios") or ():
            ents += [cp.sid(e["id"]), int(e.get("ratio") or 0), int(e.get("upgradeCount") or 0)]
        r.ent_off, _ = cp.l64(ents)
        r.n_ent = len(ents) // 3
        by[4 * n + r.id] = len(pools)
        pools.append(r)

    drinks = []
    for k, d in (master.drinks or {}).items():
        r = DrinkRow()
        r.id = cp.sid(k)
        ids = [cp.sid(master.drink_effects[x]["produceExamEffectId"]) for x in d.get("produceDrinkEffectIds") or ()]
        r.eff_off, r.n_eff = cp.l32(ids)
        by[5 * n + r.id] = len(drinks)
        drinks.append(r)

    card_enchants = []
    for sid in strings:
        if not sid.startswith("card_enchant-"): continue
        spec = card_enchant_spec(sid, master.triggers, getattr(master, "card_status_enchants", None))
        r = CardEnchantRow()
        r.id, r.trigger = cp.sid(sid), -1
        if spec:
            trigger, limit, gids, stance = spec
            r.trigger, r.limit, r.stance = cp.sid(trigger), limit, stance
            r.grow_off, r.n_grow = cp.l32([cp.sid(x) for x in gids])
        card_enchants.append(r)

    def arr(T, rows):
        a = (T * max(1, len(rows)))()
        for i, r in enumerate(rows):
            a[i] = r
        return a

    keep = {}
    md = MasterDesc()
    md.n_strings = n
    amount = int((master.exam_setting or {}).get("overPreservationReleaseToFullPowerGrowEffectLessonAdd", 10) or 0)
    md.sid_leisurely_grow = cp.sid(f"g_effect-lesson_add-{amount}") if amount > 0 else -1
    for name, T, rows in (("card_enchants", CardEnchantRow, card_enchants), ("effects", EffectRow, effects), ("triggers", TriggerRow, triggers),
                          ("enchants", EnchantRow, enchants), ("searches", SearchRow, searches),
                          ("cards", CardRow, cards), ("links", LinkRow, links), ("custs", CustRow, custs),
                          ("grows", GrowRow, grows), ("pools", PoolRow, pools), ("drinks", DrinkRow, drinks)):
        a = arr(T, rows)
        keep[name] = a
        setattr(md, name, C.cast(a, C.POINTER(T)))
        setattr(md, "n_" + name, len(rows))
    es = master.exam_setting
    md.es_fp_reduce_cancel_disable = int(bool(es.get("examFullPowerPointReduceCancelDisable", False)))
    panic_values = [int(x) for x in (es.get("produceExamPanicStaminaCandidates") or ())]
    panic_off, panic_n = cp.l64(panic_values)
    p32 = (I32 * max(1, len(cp.p32)))(*cp.p32)
    p64 = (I64 * max(1, len(cp.p64)))(*cp.p64)
    keep["p32"], keep["p64"], keep["by"] = p32, p64, by
    md.pool32, md.n_pool32 = C.cast(p32, C.POINTER(I32)), len(cp.p32)
    md.pool64, md.n_pool64 = C.cast(p64, C.POINTER(I64)), len(cp.p64)
    md.by_sid = C.cast(by, C.POINTER(I32))
    md.es_param_buff_permil = int(es.get("examParameterBuffPermil") or 0)
    md.es_param_buff_multiple_per_turn_permil = int(es.get("examParameterBuffMultiplePerTurnPermil") or 0)
    md.es_sc_add_permil = int(es.get("examStaminaConsumptionAddPermil") or 0)
    md.es_buff_add_permil = int(es.get("examBuffConsumptionAddPermil", 1000))
    for k in range(4):
        md.es_fusion_limit[k] = int(es.get(f"examPlan4Group{k + 1}FusionLimit") or 0)
    for k, name in enumerate(("BeforeTurnPermil", "BeforeBuffValueAdd", "BeforeParameterDownPermil",
                              "AfterBuffValueReduce", "AfterParameterUpPermil")):
        md.es_meta[k] = int(es.get("examMetamorphose" + name) or 0)
    md.es_sc_add_down_permil = int(es.get("examStaminaConsumptionAddDownPermil") or 0)
    md.es_sc_down_permil = int(es.get("examStaminaConsumptionDownPermil") or 0)
    md.es_sc_down_add_permil = int(es.get("examStaminaConsumptionDownAddPermil") or 0)
    md.es_turn_end_recovery = int(es.get("examTurnEndRecoveryStamina", 2))
    md.es_gimmick_param_debuff_permil = int(es.get("examGimmickParameterDebuffPermil") or 0)
    md.es_block_add_down_permil = int(es.get("examBlockAddDownPermil", 667))
    md.es_conc_penetrate_reduce2 = int(es.get("examConcentrationStaminaPenetrateReduce2") or 0)
    md.es_conc_lesson1 = int(es.get("examConcentrationLessonValueMultiplePermil1") or 0)
    md.es_conc_lesson2 = int(es.get("examConcentrationLessonValueMultiplePermil2") or 0)
    md.es_pres_lesson1 = int(es.get("examPreservationLessonValueMultiplePermil1") or 0)
    md.es_pres_lesson2 = int(es.get("examPreservationLessonValueMultiplePermil2") or 0)
    md.es_fp_lesson = int(es.get("examFullPowerLessonValueMultiplePermil") or 0)
    md.es_conc_stamina1 = int(es.get("examConcentrationStaminaMultiplePermil1") or 0)
    md.es_conc_stamina2 = int(es.get("examConcentrationStaminaMultiplePermil2") or 0)
    md.es_pres_stamina1 = int(es.get("examPreservationStaminaMultiplePermil1") or 0)
    md.es_pres_stamina2 = int(es.get("examPreservationStaminaMultiplePermil2") or 0)
    md.es_stamina_reduce_change = int(es.get("examStaminaReduceChange") or 0)
    md.es_conc_stamina_penetrate_reduce2 = int(es.get("examConcentrationStaminaPenetrateReduce2") or 0)
    md.es_lpm_depend_permil = int(es.get("examLessonValueMultipleDependReviewOrAggressiveMultiplePermil") or 0)
    md.es_lpm_depend_max_permil = int(es.get("examLessonValueMultipleDependReviewOrAggressiveMaxPermil") or 0)
    md.es_over_pres_block_add = int(es.get("overPreservationReleaseBlockAdd") or 0)
    for field, key, default in (("es_pres_release_enth1", "preservationReleaseEnthusiastic1", 5),
                                ("es_pres_release_enth2", "preservationReleaseEnthusiastic2", 8),
                                ("es_pres_release_block2", "preservationReleaseBlockAdd2", 5),
                                ("es_pres_release_playable1", "preservationReleasePlayableValueAdd1", 1),
                                ("es_pres_release_playable2", "preservationReleasePlayableValueAdd2", 1),
                                ("es_over_pres_release_enth", "overPreservationReleaseEnthusiastic", 10),
                                ("es_over_pres_release_playable", "overPreservationReleasePlayableValueAdd", 1),
                                ("es_fp_playable_add", "fullPowerPlayableValueAdd", 1)):
        setattr(md, field, int(es[key] if es.get(key) is not None else default))
    md.panic_candidates_off, md.n_panic_candidates = panic_off, panic_n
    setting = getattr(master, "setting", None) or {}
    md.setting_score_threshold_multiple = int(setting.get("examBattleConditionThresholdMultipleScore", 1000))
    md.sid_empty, md.sid_drink, md.sid_memory = cp.sid(""), cp.sid("drink"), cp.sid("memory")
    md.sid_post_play, md.sid_reset_hand = cp.sid("post_play"), cp.sid("reset_hand")
    md.sid_trg_trouble2 = cp.sid(FIXED_STRINGS[5])
    md.sid_trg_lb6 = cp.sid(FIXED_STRINGS[6])
    md.sid_trg_pb4 = cp.sid(FIXED_STRINGS[7])
    for i, name in enumerate(TIMED_GAIN_NAMES):
        md.sid_tg[i] = cp.sid(name)
    for i in range(220):
        md.sid_debuff_kind[i] = cp.sid(DEBUFF_KIND[i]) if i in DEBUFF_KIND else -1
    return md, keep


def _synchro_round(session):
    """a card of the round (any zone, the deck's cards) is a シンクロ card: a plan4GroupType, the Plan4 plan
    (5) or the Derive category (4)."""
    master = getattr(session, "master", None)
    rows = getattr(master, "cards", None) or {}
    deck = session.deck
    cards = []
    for name in ("deck", "hand", "grave", "hold", "lost"):
        cards += list(getattr(deck, name, None) or ())
    for card in cards:
        row = rows.get((card.get("produce_card_id"), int(card.get("current_upgrade_count") or 0))) \
            or rows.get((card.get("produce_card_id"), 0)) or {}
        if row.get("plan4GroupType") or _num(row.get("planType")) == 5 or _num(row.get("category")) == 4:
            return True
    cfg, produce = getattr(session, "config", None), getattr(session, "produce", None)
    items = getattr(produce, "item_enchant_ids", None)
    items = list(items(master) if callable(items) else items or ())
    enchants = getattr(master, "enchants", None) or {}
    effects = getattr(master, "effects", None) or {}
    for eid in items + list(getattr(cfg, "lesson_start_enchant_ids", None) or ()):
        for e in (enchants.get(eid) or {}).get("produceExamEffectIds") or ():
            if _num((effects.get(e) or {}).get("effectType")) == 237:
                return True
    return False


def compile_round(session, strings_index, n_strings):
    cfg = session.config
    deck = session.deck
    r = RoundDesc()
    r.limit_turn = int(cfg.limit_turn)
    r.final_audition = int(bool(cfg.final_audition))
    r.skip_clears_adds = int(getattr(cfg, "skip_clears_playable_adds", 0) or 0)
    if cfg.score_cap is not None:
        r.has_score_cap, r.score_cap = 1, int(cfg.score_cap)
    if cfg.parameter_type is not None:
        r.has_cfg_pt, r.cfg_pt = 1, int(cfg.parameter_type)
    if cfg.turn_parameter_types:
        last = max(int(t) for t in cfg.turn_parameter_types)
        if last > 40 or min(int(t) for t in cfg.turn_parameter_types) < 1:
            raise ValueError("round: turn_parameter_types beyond turn 40")
        r.n_cfg_turn_pt = last
        for i in range(last):
            r.cfg_turn_pt[i] = -(2 ** 63)
        for t, pt in cfg.turn_parameter_types.items():
            if pt is not None:
                r.cfg_turn_pt[int(t) - 1] = int(pt)
    for pt, pm in (cfg.audition_permils or {}).items():
        r.permil[int(pt)], r.has_permil[int(pt)] = int(pm), 1
    sups = deck.eligible_supports or []
    if len(sups) > 8:
        raise ValueError("round: > 8 supports")
    r.n_supports = len(sups)
    for i, s in enumerate(sups):
        r.supports[i].row = strings_index[s["support_row_id"]]
        r.supports[i].permil = int(s["permil"])
        pt = s.get("parameter_type")
        r.supports[i].parameter_type = -1 if pt is None else int(pt)
        r.supports[i].csp = int(s["card_search_position_type"])
    mask = (U8 * n_strings)()
    for cid, u in deck.upgrade_tiers:
        if cid in strings_index and 0 <= int(u) < 8:
            mask[strings_index[cid]] |= 1 << int(u)
    r.tier_mask = C.cast(mask, C.POINTER(U8))
    r.play_move_position_type_enabled = int(bool(deck.play_move_position_type_enabled))
    rows = sorted((dict(g) for g in (session.state.gimmick_rows or ())),
                  key=lambda g: (int(g["startTurn"]), int(g["priority"])))
    if len(rows) > 32:
        raise ValueError(f"round: {len(rows)} gimmick rows > capacity 32")
    ga = (GimmickRow * max(1, len(rows)))()
    for i, g in enumerate(rows):
        field = g.get("fieldStatusType") or {}
        check = g.get("fieldStatusCheckType") or {}
        ga[i].start_turn = int(g["startTurn"])
        ga[i].priority = int(g["priority"])
        ga[i].field_status_type = int(field.get("number", -1)) if field else -1
        ga[i].field_status_value = int(g.get("fieldStatusValue") or 0)
        ga[i].field_status_check_type = int(check.get("number", -1)) if check else -1
        ga[i].is_positive = int(bool(g.get("isPositive")))
        fss = g.get("fieldStatusProduceCardSearchId")
        ga[i].field_status_search = strings_index.get(fss, -1) if fss else -1
        effect_id = g.get("produceExamEffectId")
        ga[i].effect = -1 if effect_id is None else strings_index[effect_id]
        effect = session.state.exam_effects.get(effect_id) or {}
        ga[i].pre_draw = int(_num(effect.get("effectType")) == 9
                             and "hand" not in str(effect.get("produceCardSearchId", "")))
    r.n_gimmick_rows = len(rows)
    r.gimmick_rows = C.cast(ga, C.POINTER(GimmickRow))
    r._gimmick_rows_keep = ga
    unique = [strings_index[x] for x in sorted(session.state.idol_unique_item_enchant_ids)]
    ia = (I32 * max(1, len(unique)))(*unique)
    r.n_idol_enchants, r.idol_enchants = len(unique), C.cast(ia, C.POINTER(I32))
    r._idol_enchants_keep = ia
    research = sorted(strings_index[x] for x in getattr(session.state, "research_card_ids", ()) or ()
                      if x in strings_index)
    ra = (I32 * max(1, len(research)))(*research)
    r.n_research_cards, r.research_cards = len(research), C.cast(ra, C.POINTER(I32))
    r._research_cards_keep = ra
    pol = getattr(deck, "select_policy", None)
    r.select_greedy = int(getattr(pol, "rule_name", None) == "greedy_select_policy")
    r.synchro = int(_synchro_round(session))
    if deck.recycle_order != "insertion":
        raise ValueError("round: recycle_order")
    return r, mask


def gen_header():
    from GakumasCLI.native.layout import _ctype_decl
    out = ["/* generated by GakumasCLI/native/mcompile.py -- do not edit */", "#pragma once", "#ifndef __CUDACC_RTC__",
           "#include <stdint.h>", "#endif", ""]
    for cls in (EffectRow, TriggerRow, EnchantRow, SearchRow, LinkRow, CardRow, CustRow, GrowRow, PoolRow,
                DrinkRow, Support, GimmickRow, CardEnchantRow, MasterDesc, RoundDesc):
        out.append(f"typedef struct {cls.__name__} {{")
        for fname, ftype in cls._fields_:
            if isinstance(ftype, type) and issubclass(ftype, C._Pointer):
                base = ftype._type_
                bname = {I32: "int32_t", I64: "int64_t", U8: "uint8_t"}.get(base, base.__name__)
                out.append(f"    const {bname} *{fname};")
            else:
                out.append(f"    {_ctype_decl(ftype, fname)};")
        out.append(f"}} {cls.__name__};  /* sizeof {C.sizeof(cls)} */")
        out.append("")
    return "\n".join(out) + "\n"
