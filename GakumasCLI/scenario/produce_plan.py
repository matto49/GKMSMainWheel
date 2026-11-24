# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""the produce plan (培育方案) -- what MAA does by itself during a produce and where it stops for the player, with the
player's own preferences, as one document and one share code (引继码).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import zlib

FORMAT = "gkms.produce_plan.v2"
FORMAT_V1 = "gkms.produce_plan.v1"
SHARE_PREFIX = "GKPP1:"
MAX_BYTES = 256 * 1024

KINDS = {
    "route": "日程（每一步走哪）",
    "school_event": "授業（属性、标签）",
    "activity_event": "外出 / 活動事件选项",
    "card_pick": "抓卡（三选一、礼物）",
    "card_change": "变卡（チェンジ）",
    "shop": "商店 / 相談",
    "interval": "Interval",
    "drink_pick": "饮料三选一",
    "drink_discard": "丢饮料",
    "customize": "强化 / 删除 / 改造",
    "reroll": "重抽 / 除外",
    "story": "剧情、结算、确认框",
}
MODES = {"auto": "自动", "first": "快速（拿第一个，不等搜索）", "sure": "自动（没把握就停）", "stop": "停下等我"}
FIRST_KINDS = ("card_pick", "card_change", "drink_pick", "drink_discard")
# the plan's mode and the v2 switches
PLAN_MODES = {"normal": "普通", "zemi": "老师周本战（只走本战，选拔你来）"}
CRITERIA = {"avoid": "改走能达标的", "stop": "停下等我", "ignore": "不管"}
SHOP_BUY = {"advice": "按方案 / 建议", "drinks": "只买饮料（算过值不值再买，满了丢算出来最不值的）",
            "must_only": "只买必拿 / 目标卡组的卡", "never": "不买卡"}
SHOP_DELETE = {"plan": "按评分与保护", "gained": "删本战获得的卡（常规卡和支援卡发的都算）",
               "gained_normal": "删本战获得的常规卡（支援卡发的不删）", "gained_support": "删本战支援卡发的卡"}
GAINED_DELETES = ("gained", "gained_normal", "gained_support")
SHOP_DRINKS = {"always": "有空位就买（饮料会触发支援卡加属性）；满了只在算出来不亏时换", "worth": "算下来值才买（只看审查分）"}
SHOP_REFRESH = {0: "不刷新", 1: "最多刷新 1 次", 2: "最多刷新 2 次", 3: "最多刷新 3 次"}
IDLE_TAP = {True: "开（认不出的画面一直点空白处）", False: "关"}
REFRESH_COST = 10
REFRESH_MIN_DRINK = 50
CHANGE_TARGETS = {"plan": "按评分与保护（打了分或设了保护时）", "lowest": "变掉评分最低的",
                  "first_school": "变掉本战第一次授课换来的卡",
                  "advice": "按推荐（等搜索：拿和变掉都按算出来最好的那一对）",
                  "gained": "变掉本战获得的卡（最早的那张）", "named": "变掉指定的卡（「指定变掉的卡」里第一张在牌组里的）"}
CHANGE_TARGETS_2 = dict({"same": "和第一次一样"}, **CHANGE_TARGETS)
# the memory's pages after the 本戦 (回忆变换: 重新生成 / 下一步)
MEMORY_CARD = {"auto": "自动（直接下一步，不重新生成）", "stop": "停下等我（重新生成、选哪个你来）"}
ATTR_PREFS = {"route": "按路线推荐", "vo": "Vo", "da": "Da", "vi": "Vi"}
EXAM_BUDGET = {"normal": "照常", "low": "压低预算", "short": "只跑快速轮"}
EXAM_DRINKS = {"table": "按饮料表算", "all": "全喝（不算饮料表）"}
PLAN_STEPS = {"007": 20, "008": 9}
ROUTE_MODES = {"latest": "每步按日程卡最新推荐", "locked": "开局锁定（每步可改）", "stop_each": "每步都停"}
REROLL_MODES = {"stop": "不自动重抽（要重抽自己点）", "never": "不重抽，直接选", "if_no_target": "报价里没有目标卡才重抽",
                "by_v": "按选卡包 V：新报价更可能有好卡才重抽"}
MAA_MODES = {"keep": "沿用现在的设置", "off": "关", "watch": "只看", "auto": "自动出牌",
             "layout": "自动出牌（按坐标）"}
GOAL_KINDS = {"hif_score": "H.I.F 高分", "memory": "记忆（メモリー）"}
MEMORY_GRADES = ("B", "B+", "A", "A+", "S", "S+", "SS", "SS+", "SSS", "SSS+", "S4", "S4+", "S5")
SHOP_ITEMS = ("drink", "stamina", "card", "upgrade", "delete", "change")
ATTRS = ("vo", "da", "vi")
STAGES = ("hif.sel1", "hif.sel2", "hif.sel3", "hif.main1", "hif.main2", "*")
ROUTE_CHOICES = {
    "vo": "Vo 课（有 SP 先 SP）", "da": "Da 课（有 SP 先 SP）", "vi": "Vi 课（有 SP 先 SP）",
    "school_vo": "授業 Vo", "school_da": "授業 Da", "school_vi": "授業 Vi",
    "activity": "外出（活動）", "present": "礼物", "shop": "商店 / 相談", "rest": "休息", "customize": "改造",
    "business": "营业",
}
# every candidate name the game sends (produce_ledger.STEP_TYPE's values)
STEP_NAMES = frozenset(
    ["lesson_vo", "lesson_vo_sp", "lesson_vo_hard", "lesson_da", "lesson_da_sp", "lesson_da_hard", "lesson_vi",
     "lesson_vi_sp", "lesson_vi_hard", "event", "event_activity", "event_school", "shop", "refresh", "present",
     "audition_mid1", "audition_mid2", "audition_final", "self_lesson_vo", "self_lesson_vo_sp", "self_lesson_da",
     "self_lesson_da_sp", "self_lesson_vi", "self_lesson_vi_sp", "business", "event_business", "fan_present",
     "customize", "interval", "event_school_vo", "event_school_da", "event_school_vi"]
    + [f"open_lesson_{a}{v}" for a in ATTRS for v in ("", "_sp", "_star", "_sp_star")]
    + [f"legend_lesson_{a}{v}" for a in ATTRS for v in ("", "_sp")])
HIF_LAYOUT = {
    "produce-007": [
        ("present", "shop", "rest", "customize"), ("lesson*", "rest"), ("school", "rest"), ("lesson", "rest"),
        ("activity", "shop", "rest"), ("school", "rest"), ("audition_mid1",),
        ("activity", "present", "rest"), ("lesson*", "rest"), ("school", "rest"), ("lesson", "rest"),
        ("shop", "rest", "customize"), ("audition_mid2",),
        ("activity", "present", "rest"), ("lesson*", "rest"), ("activity", "present", "shop", "rest"),
        ("school", "rest"), ("lesson", "rest"), ("shop", "rest", "customize"), ("audition_final",)],
    "produce-008": [
        ("school", "rest"), ("lesson*", "rest"), ("activity", "present", "rest"), ("school", "rest"),
        ("lesson", "rest"), ("shop", "rest"), ("audition_mid1",), ("interval",), ("audition_final",)],
}
SCHOOL_TAGS = {"advice": "按建议", "parameter_buff": "好调", "lesson_buff": "集中", "review": "好印象",
               "card_play_aggressive": "干劲", "concentration": "强气", "full_power": "全力"}
SCORE_TIERS = (("S", 90), ("A", 80), ("B", 60), ("C", 0))
SCORE_ROLES = {"auto": "按目标（记忆：分主导；有选卡包时：打过分就分主导，没打过就按 V；没有选卡包的 H.I.F 高分：搜索分不出时才看分）",
               "lead": "分主导", "tiebreak": "V（没有选卡包时搜索）优先，分不出时看分"}
SCORE_DEFAULTS = {"unlisted": "v", "r": 10, "tag": 70, "convertible": 15, "trouble": 0}
V_DEFAULTS = ("unlisted", "r", "tag", "convertible")
TIE_MARGIN = 5

_ALL_STOP = {k: "stop" for k in KINDS}
_V1_BASE = {
    "format": FORMAT_V1,
    "scope": {"scenarios": ["hif"], "idols": [], "plans": []},
    "stops": dict(_ALL_STOP, story="auto"),
    "conditions": {"wait_seconds": 20, "stop_on_triangle": True, "stop_on_unknown_cards": True},
    "route": {"mode": "stop_each", "steps": {}, "avoid": [], "attr_weight": {"vo": 1.0, "da": 1.0, "vi": 1.0}},
    "school": {"tag": "advice", "attr": "route"},
    "events": {},
    "cards": {"must": [], "prefer": {}, "avoid": [], "never": [], "deck_cap": 0, "target": [],
              "scores": {}, "defaults": dict(SCORE_DEFAULTS), "score_role": "auto", "min_take": 0,
              "protect": {"at": 80, "no_change": [], "no_delete": []}, "convertible": []},
    "shop": {"reserve_p": 0, "order": [], "buy_cards": "advice"},
    "drinks": {"keep": [], "discard_first": []},
    "customize": {"targets": []},
    "reroll": {"mode": "stop"},
    "exam": {"maa": "keep", "timeline": None, "timelines": {}},
    "goal": {"kind": "hif_score", "grade": "SSS", "cards": [], "unique": None},
    "formation": {"slot": None},
    "zemi": {"on": False, "route": {}, "fast_pick": True, "shop": "drinks", "delete_gained": False,
             "refresh_for_delete": False, "change_target": "first_school", "exam_budget": "short",
             "retry_lines": [700000, 1400000], "interval_customize": [], "allow_support": False,
             "drinks_all": True, "skip_final": False},
}


def _v1_preset(pid, name, note, **changes):
    doc = copy.deepcopy(_V1_BASE)
    for path, value in changes.items():
        node = doc
        keys = path.split("__")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
    doc.update(id=pid, name=name, note=note)
    return doc


_V1_PRESETS = {p["id"]: p for p in (
    _v1_preset("pp_assist", "只给建议", ""),
    _v1_preset("pp_route", "", "", route__mode="latest",
               stops=dict(_ALL_STOP, route="auto", school_event="auto", story="auto")),
    _v1_preset("pp_auto_sure", "", "", route__mode="latest",
               stops=dict({k: "sure" for k in KINDS}, route="auto", school_event="auto", story="auto", reroll="stop")),
    _v1_preset("pp_full_auto", "", "", route__mode="latest", stops=dict({k: "auto" for k in KINDS}, reroll="auto"),
               reroll__mode="never",
               conditions={"wait_seconds": 20, "stop_on_triangle": False, "stop_on_unknown_cards": False}),
    _v1_preset("pp_zemi", "", "", route__mode="latest", stops=dict({k: "auto" for k in KINDS}, reroll="auto"),
               reroll__mode="never",
               conditions={"wait_seconds": 20, "stop_on_triangle": False, "stop_on_unknown_cards": False},
               zemi__on=True))}
LOW_BUDGET = {"open_seconds": 60, "open_runs": 1, "resolve_seconds": 10}
RETRY_STAGES = ("hif.sel1", "hif.sel2", "hif.sel3", "hif.main1", "hif.main2")
# v1's zemi.fast_pick took these by the rule
V1_FAST_KINDS = ("card_pick", "card_change", "drink_discard")


def migrate(v1):
    """a v1 plan (resolved over the v1 presets) as a v2 plan -- the same answers."""
    v1 = copy.deepcopy(v1)
    z = v1.pop("zemi", None) or {}
    c = v1.pop("conditions", None) or {}
    on = bool(z.get("on"))
    v2 = v1
    v2["format"] = FORMAT
    v2["mode"] = "zemi" if on else "normal"
    if v2.get("base") == "pp_assist":
        v2["base"] = "pp_route"
    v2["wait_seconds"] = c.get("wait_seconds", 20)
    route = v2.setdefault("route", {})
    route["criteria"] = ("stop" if c.get("stop_on_triangle") else
                         "avoid" if c.get("avoid_triangle", True) else "ignore")
    route.setdefault("lesson_attr", "route")
    stops = v2.setdefault("stops", {})
    if c.get("stop_on_unknown_cards", False):
        for k in ("card_pick", "card_change"):
            if stops.get(k) == "auto":
                stops[k] = "sure"
    shop = v2.setdefault("shop", {})
    buy = shop.pop("buy_cards", "advice")
    shop["buy"] = "drinks" if on and z.get("shop", "drinks") == "drinks" else buy
    shop["delete"] = "gained" if on and z.get("delete_gained") else "plan"
    shop["refresh_for_delete"] = bool(on and z.get("refresh_for_delete"))
    shop["drinks"] = "always"
    shop["refresh"] = 1
    v2["idle_tap"] = True
    v2["memory"] = "auto"
    v2.setdefault("cards", {})["change_target"] = (
        ("first_school" if z.get("change_target", "first_school") == "first_school" else "lowest") if on else "plan")
    v2["cards"]["change_target_2"] = "same"
    v2["cards"].setdefault("change_cards", [])
    v2["interval"] = {"customize": list(z.get("interval_customize") or []) if on else [],
                      "allow_support": bool(on and z.get("allow_support"))}
    exam = v2.setdefault("exam", {})
    exam["budget"] = z.get("exam_budget", "short") if on else "normal"
    exam["drinks"] = "all" if on and z.get("drinks_all", True) else "table"
    lines = list(z.get("retry_lines") or []) if on else []
    exam["retry"] = {"lines": {st: int(x) for st, x in zip(("hif.main1", "hif.main2"), lines)
                               if isinstance(x, int) and not isinstance(x, bool) and x > 0}}
    exam["skip"] = ["hif.main2"] if on and z.get("skip_final") else []
    exam.setdefault("split_preset", None)
    if on:
        steps = route.setdefault("steps", {})
        for k, v in (z.get("route") or {}).items():
            steps[f"008:{k}"] = v
        if z.get("fast_pick", True):
            for k in V1_FAST_KINDS:
                stops[k] = "first"
    return v2


def _preset(pid, name, note, group, v1=None, **changes):
    doc = migrate(_V1_PRESETS[v1 or pid])
    for path, value in changes.items():
        node = doc
        keys = path.split("__")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
    doc.update(id=pid, name=name, note=note, group=group)
    return doc


PRESETS = [
    _preset("pp_route", "自动走路线（默认）", "按日程卡走、授業自动、剧情跳过；抓卡、外出、变卡、商店、饮料、改造停下等你", "普通"),
    _preset("pp_auto_sure", "全自动（没把握就停）", "全部自动；建议没分出胜负时停下；不自动重抽", "普通"),
    _preset("pp_full_auto", "全自动", "全部自动，不重抽；推荐路线到审查时参数不达标就改走达标的；只在跟丢、认不出画面时停",
            "普通"),
    _preset("pp_zemi", "全自动", "只走本战：选卡拿第一张、饮料等搜索，咨询和幕间只买算过的饮料，审查只跑快速轮、饮料全喝，"
            "打不到线用重来券；选拔你来", "老师周本战"),
    _preset("pp_zemi_skip", "全自动，跳过下半场", "同上；下半场不搜索、每回合直接结束、不用重来券", "老师周本战", v1="pp_zemi",
            exam__skip=["hif.main2"]),
]
_BASE = migrate(_V1_BASE)
PRESET_IDS = [p["id"] for p in PRESETS]
DEFAULT_PRESET = "pp_route"

FORM = [
    ("goal.kind", "培育目标", GOAL_KINDS, "目标", None),
    ("route.mode", "路线", ROUTE_MODES, "路线", None),
    ("route.criteria", "到审查时参数不达标", CRITERIA, "路线", None),
    ("route.lesson_attr", "训练属性", ATTR_PREFS, "路线", None),
    ("stops.school_event", KINDS["school_event"], MODES, "事件", None),
    ("school.attr", "授業属性", ATTR_PREFS, "事件", None),
    ("school.tag", "授業标签", SCHOOL_TAGS, "事件", None),
    ("stops.activity_event", KINDS["activity_event"], MODES, "事件", None),
    ("stops.card_pick", KINDS["card_pick"], MODES, "选卡与饮料", None),
    ("stops.card_change", KINDS["card_change"], MODES, "选卡与饮料", None),
    ("cards.change_target", "变卡变掉哪张", CHANGE_TARGETS, "选卡与饮料", None),
    ("cards.change_target_2", "第二次起变掉哪张", CHANGE_TARGETS_2, "选卡与饮料", None),
    ("stops.drink_pick", KINDS["drink_pick"], MODES, "选卡与饮料", None),
    ("stops.drink_discard", KINDS["drink_discard"], MODES, "选卡与饮料", None),
    ("reroll.mode", KINDS["reroll"], REROLL_MODES, "选卡与饮料", None),
    ("stops.shop", KINDS["shop"], MODES, "咨询与幕间", None),
    ("stops.interval", KINDS["interval"], MODES, "咨询与幕间", None),
    ("stops.customize", KINDS["customize"], MODES, "咨询与幕间", None),
    ("shop.buy", "咨询 / 幕间买什么", SHOP_BUY, "咨询与幕间", None),
    ("shop.drinks", "买饮料", SHOP_DRINKS, "咨询与幕间", None),
    ("shop.refresh", "没饮料可买时刷新", SHOP_REFRESH, "咨询与幕间", None),
    ("shop.delete", "咨询删卡", SHOP_DELETE, "咨询与幕间", ("zemi",)),
    ("exam.maa", "审查出牌（MAA）", MAA_MODES, "审查", None),
    ("exam.timeline", "审查时间轴", None, "审查", None),
    ("exam.split_preset", "控分预设", None, "审查", None),
    ("exam.drinks", "审查饮料", EXAM_DRINKS, "审查", None),
    ("exam.budget", "审查搜索", EXAM_BUDGET, "审查", None),
    ("exam.skip", "下半场", {(): "照常打", ("hif.main2",): "直接跳过（每回合结束，不用重来券）"}, "审查", ("zemi",)),
    ("wait_seconds", "建议还在算时等几秒", None, "等待", None),
    ("idle_tap", "MAA 空闲点击", IDLE_TAP, "等待", None),
    ("memory", "回忆卡（回忆变换）", MEMORY_CARD, "等待", None),
]


class PlanError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors[:5]) + (" ..." if len(self.errors) > 5 else ""))


# ------------------------------------------------------------------ documents
def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(obj):
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def preset(pid):
    for p in PRESETS:
        if p["id"] == pid:
            return copy.deepcopy(p)
    raise PlanError([f"没有这个预设: {pid}"])


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("steps", "events", "prefer", "timelines"):
            out[k] = _merge(out[k], v)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = dict(out[k], **v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _diff(base, doc):
    out = {}
    for k, v in doc.items():
        if k not in base:
            out[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(base[k], dict):
            d = _diff(base[k], v)
            if d:
                out[k] = d
        elif v != base[k]:
            out[k] = copy.deepcopy(v)
    return out


# a document with any of these is v2
_V2_KEYS = {"": ("mode", "wait_seconds", "interval", "idle_tap", "memory"), "route": ("criteria", "lesson_attr"),
            "shop": ("buy", "delete", "refresh_for_delete", "drinks", "refresh"),
            "cards": ("change_target", "change_target_2", "change_cards"),
            "exam": ("budget", "drinks", "retry", "skip", "split_preset")}
_V1_KEYS = {"": ("zemi", "conditions"), "shop": ("buy_cards",)}


def _has(doc, keys):
    for sec, names in keys.items():
        node = doc if not sec else doc.get(sec)
        if isinstance(node, dict) and any(n in node for n in names):
            return True
    return False


def is_v1(doc):
    """Whether DOC is a v1 document (format v1; or no format, no ``"v": 2`` and no v2 field)."""
    fmt = doc.get("format")
    if fmt == FORMAT or doc.get("v") == 2:
        return False
    if fmt == FORMAT_V1 or _has(doc, _V1_KEYS):
        return True
    if doc.get("base") and doc["base"] not in _V1_PRESETS:
        return False
    return not _has(doc, _V2_KEYS)


def resolve(doc):
    """The full plan: the preset named by `base` (default pp_route) with the document's fields over it."""
    doc = doc or {}
    if not isinstance(doc, dict):
        raise PlanError(["方案要是一个对象"])
    if is_v1(doc):
        base_id = doc.get("base") or DEFAULT_PRESET
        if base_id not in _V1_PRESETS:
            raise PlanError([f"base 应为预设之一: {', '.join(PRESET_IDS)}"])
        over = {k: v for k, v in doc.items() if k not in ("base", "format")}
        try:
            full = migrate(dict(_merge(_V1_PRESETS[base_id], over), base=base_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise PlanError([f"旧格式（v1）的方案转换不了: {type(exc).__name__}: {exc}"]) from exc
    else:
        if _has(doc, _V1_KEYS):
            raise PlanError(["zemi / conditions / shop.buy_cards 是旧格式（v1）的字段：改写成 v2 的字段，"
                             "或去掉 format 按旧格式读"])
        base = preset(doc.get("base") or DEFAULT_PRESET)
        over = {k: v for k, v in doc.items() if k not in ("base", "format", "v")}
        full = _merge(base, over)
        full["base"] = doc.get("base") or DEFAULT_PRESET
    full["format"] = FORMAT
    for k in ("id", "name", "note", "group"):
        full.pop(k, None)
    validate(full)
    return full


def compact(full):
    """The short document of a full plan: `base` and what differs from it (what a share code carries)."""
    base_id = full.get("base") or DEFAULT_PRESET
    base = preset(base_id)
    for k in ("id", "name", "note", "group"):
        base.pop(k, None)
    d = _diff(base, {k: v for k, v in full.items() if k not in ("base", "format", "id", "name", "note", "group")})
    return dict({"format": FORMAT, "base": base_id}, **d)


def plan_id(doc):
    return "pp_" + digest(compact(resolve(doc)))[:12]


def share_code(doc):
    body = dict({k: v for k, v in compact(resolve(doc)).items() if k != "format"}, v=2)
    raw = canonical(body).encode("utf-8")
    return SHARE_PREFIX + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")


def from_share_code(text):
    text = str(text or "").strip()
    if not text.startswith(SHARE_PREFIX):
        raise PlanError([f"引继码要以 {SHARE_PREFIX} 开头"])
    body = text[len(SHARE_PREFIX):]
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)), bufsize=MAX_BYTES)
        if len(raw) > MAX_BYTES:
            raise ValueError("too large")
        doc = json.loads(raw.decode("utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("not an object")
    except Exception as exc:                                    # noqa: BLE001
        raise PlanError([f"引继码无法解开: {type(exc).__name__}"]) from exc
    if doc.get("v") == 2:
        return resolve(dict({k: v for k, v in doc.items() if k != "v"}, format=FORMAT))
    return resolve(dict(doc, format=FORMAT_V1))


def from_form(values, base=DEFAULT_PRESET, doc=None):
    """A plan from the start form's values {path: value}; values equal to the preset's are dropped."""
    doc = copy.deepcopy(compact(resolve(doc))) if doc else {"base": base, "format": FORMAT}
    for path, value in (values or {}).items():
        node = doc
        keys = path.split(".")
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value
    return compact(resolve(doc))


def _split_preset_choices(value, split_preset):
    """the 控分预设 row -- none, the plan's own preset, the one in force on the station (SPLIT_PRESET)."""
    from GakumasCLI.scenario import split_preset as SPL
    opts = [[None, "不带"]]
    for code, note in ((value, ""), (SPL.full_code(split_preset) if split_preset else None, "（当前生效）")):
        if not code or any(o[0] == code for o in opts):
            continue
        try:
            opts.append([code, f"带「{SPL.from_full_code(code)['name']}」{note}"])
        except SPL.PresetError:
            continue
    return opts


def form_view(doc, timelines=(), split_preset=None):
    """The start form for a plan: [{path, label, value, choices: [[value, label],...], group}]; TIMELINES = [(id, name)].
    """
    full = resolve(doc)
    out = []
    for path, label, choices, group, modes in FORM:
        if modes is not None and full.get("mode") not in modes:
            continue
        if choices is MODES and path.split(".")[-1] not in FIRST_KINDS:
            choices = {k: v for k, v in MODES.items() if k != "first"}
        node = full
        for k in path.split("."):
            node = node.get(k) if isinstance(node, dict) else None
        if path == "exam.timeline":
            from GakumasCLI.scenario import timeline as TL
            opts = [[None, "无（默认）"]] + [[p["id"], p["name"]] for p in TL.PRESETS] + [list(t) for t in timelines]
        elif path == "exam.split_preset":
            opts = _split_preset_choices(node, split_preset)
        elif choices is None:
            opts = None
        else:
            opts = [[list(k) if isinstance(k, tuple) else k, v] for k, v in choices.items()]
        out.append({"path": path, "label": label, "value": node if not isinstance(node, dict) else "自定义",
                    "choices": opts, "group": group})
    return out


# ------------------------------------------------------------------ validation
def _card_ok(key):
    return isinstance(key, str) and re.match(r"^p_card-[0-9a-z]+-[a-z]+-[0-9_]+(\+[0-9])?$", key)


def _step_ok(key):
    return isinstance(key, str) and re.match(r"^(00[78]):([0-9]{1,2})$", key)


def _shape_errors(base, doc, where=""):
    """where ``doc`` has a value that is not the shape the preset ``base`` has there -- not an object where the preset
    has one, not a list where it has a list -- the rest of validate would read it as one."""
    e = []
    for k, bv in base.items():
        dv = doc.get(k)
        at = f"{where}{k}"
        if dv is None:
            continue
        if isinstance(bv, dict):
            if not isinstance(dv, dict):
                e.append(f"{at}: 应为对象（{{…}}），现在是 {type(dv).__name__}")
            else:
                e.extend(_shape_errors(bv, dv, at + "."))
        elif isinstance(bv, list) and not isinstance(dv, list):
            e.append(f"{at}: 应为列表（[…]），现在是 {type(dv).__name__}")
    return e


def validate(full):
    """Every problem, in Chinese, at once (PlanError); returns the plan."""
    shape = _shape_errors(preset(full.get("base") if full.get("base") in PRESET_IDS else DEFAULT_PRESET), full)
    if shape:
        raise PlanError(shape)
    e = []
    if full.get("format") != FORMAT:
        e.append(f"format 应为 {FORMAT}")
    if full.get("base") not in PRESET_IDS:
        e.append(f"base 应为预设之一: {', '.join(PRESET_IDS)}")
    if full.get("mode") not in PLAN_MODES:
        e.append(f"mode 应为 {' / '.join(PLAN_MODES)}")
    for k, v in (full.get("stops") or {}).items():
        if k not in KINDS:
            e.append(f"stops.{k}: 没有这类决策（{', '.join(KINDS)}）")
        elif v not in MODES or (v == "first" and k not in FIRST_KINDS):
            e.append(f"stops.{k}: 应为 {'/'.join(m for m in MODES if m != 'first' or k in FIRST_KINDS)}")
    w = full.get("wait_seconds")
    if not isinstance(w, (int, float)) or isinstance(w, bool) or not 0 <= w <= 300:
        e.append("wait_seconds: 0–300 秒")
    r = full.get("route") or {}
    if r.get("mode") not in ROUTE_MODES:
        e.append(f"route.mode 应为 {'/'.join(ROUTE_MODES)}")
    if r.get("criteria") not in CRITERIA:
        e.append(f"route.criteria 应为 {' / '.join(CRITERIA)}")
    if r.get("lesson_attr") not in ATTR_PREFS:
        e.append("route.lesson_attr: route / vo / da / vi")
    for k, v in (r.get("steps") or {}).items():
        if not _step_ok(k) or not 1 <= int(k.split(":")[1]) <= PLAN_STEPS[k.split(":")[0]]:
            e.append(f"route.steps.{k}: 写成 007:5（选拔第 1–20 步）或 008:3（本战第 1–9 步）")
        if not isinstance(v, str) or not (v in ROUTE_CHOICES or v in STEP_NAMES):
            e.append(f"route.steps.{k}: 写 {' / '.join(ROUTE_CHOICES)} 或日程候选名（如 open_lesson_vo_sp）")
    for v in r.get("avoid") or ():
        if not isinstance(v, str) or not (v in ROUTE_CHOICES or v in STEP_NAMES):
            e.append(f"route.avoid: {v} 不是 {' / '.join(ROUTE_CHOICES)} 或日程候选名")
    for k, v in (r.get("attr_weight") or {}).items():
        if k not in ATTRS or not isinstance(v, (int, float)) or not 0 <= v <= 5:
            e.append(f"route.attr_weight.{k}: vo / da / vi，0–5")
    sch = full.get("school") or {}
    if sch.get("attr") not in ("route",) + ATTRS:
        e.append("school.attr: route / vo / da / vi")
    if sch.get("tag") not in SCHOOL_TAGS:
        e.append(f"school.tag: {' / '.join(SCHOOL_TAGS)}")
    for k, v in (full.get("events") or {}).items():
        if not isinstance(v, int) or not 0 <= v <= 5:
            e.append(f"events.{k}: 选项序号 0–5")
    cards = full.get("cards") or {}
    for lst in ("must", "avoid", "never"):
        for x in cards.get(lst) or ():
            if not _card_ok(x):
                e.append(f"cards.{lst}: {x} 不是卡 id（p_card-…，可带 +1）")
    for x, w in (cards.get("prefer") or {}).items():
        if not _card_ok(x) or not isinstance(w, (int, float)) or not -5 <= w <= 5:
            e.append(f"cards.prefer.{x}: 卡 id，权重 −5..+5")
    e.extend(_target_errors(cards.get("target")))
    e.extend(_goal_errors(full.get("goal")))
    both = set(cards.get("must") or ()) & set(cards.get("never") or ())
    if both:
        e.append(f"cards: 同时在 must 和 never 里: {', '.join(sorted(both))}")
    if not isinstance(cards.get("deck_cap"), int) or not 0 <= cards["deck_cap"] <= 40:
        e.append("cards.deck_cap: 0（不限）或 1–40 张")
    e.extend(_score_errors(cards))
    slot = (full.get("formation") or {}).get("slot")
    if slot is not None and not (isinstance(slot, int) and not isinstance(slot, bool) and 1 <= slot <= 20):
        e.append("formation.slot: 回忆编成第几格（1–20），不核对写 null")
    unique = (full.get("goal") or {}).get("unique")
    if unique is not None and not (_card_ok(unique) and "-ido-" in unique):
        e.append("goal.unique: 记忆要留下的那张偶像固有卡（p_card-…-ido-…），不指定写 null")
    dr = full.get("drinks") or {}
    for lst in ("keep", "discard_first"):
        if not isinstance(dr.get(lst), list) or not all(isinstance(x, str) and x.startswith("pdrink") for x in dr[lst]):
            e.append(f"drinks.{lst}: 饮料 id 的列表（pdrink_…）")
    both = set(dr.get("keep") or ()) & set(dr.get("discard_first") or ())
    if both:
        e.append(f"drinks: 同时在 keep 和 discard_first 里: {', '.join(sorted(both))}")
    sh = full.get("shop") or {}
    if not isinstance(sh.get("reserve_p"), int) or sh["reserve_p"] < 0:
        e.append("shop.reserve_p: 不小于 0 的 P 点")
    for x in sh.get("order") or ():
        if x not in SHOP_ITEMS:
            e.append(f"shop.order: {x} 不在 {'/'.join(SHOP_ITEMS)}")
    if sh.get("buy") not in SHOP_BUY:
        e.append(f"shop.buy: {' / '.join(SHOP_BUY)}")
    if sh.get("delete") not in SHOP_DELETE:
        e.append(f"shop.delete: {' / '.join(SHOP_DELETE)}")
    if sh.get("drinks") not in SHOP_DRINKS:
        e.append(f"shop.drinks: {' / '.join(SHOP_DRINKS)}")
    if not isinstance(sh.get("refresh_for_delete"), bool):
        e.append("shop.refresh_for_delete: 写 true / false")
    rf = sh.get("refresh")
    if not isinstance(rf, int) or isinstance(rf, bool) or rf not in SHOP_REFRESH:
        e.append("shop.refresh: 0–3（每次咨询 / 幕间最多刷新几次找饮料）")
    if not isinstance(full.get("idle_tap"), bool):
        e.append("idle_tap: 写 true / false")
    if cards.get("change_target") not in CHANGE_TARGETS:
        e.append(f"cards.change_target: {' / '.join(CHANGE_TARGETS)}")
    if cards.get("change_target_2", "same") not in CHANGE_TARGETS_2:
        e.append(f"cards.change_target_2: {' / '.join(CHANGE_TARGETS_2)}")
    cc = cards.get("change_cards", [])
    if not isinstance(cc, list) or any(not isinstance(k, str) or not _card_ok(k.split("+")[0]) for k in cc):
        e.append("cards.change_cards: 卡的列表（写 p_card-… 或 p_card-…+1）")
    if full.get("memory", "auto") not in MEMORY_CARD:
        e.append(f"memory: {' / '.join(MEMORY_CARD)}")
    iv = full.get("interval") or {}
    for c in iv.get("customize") or ():
        if not _card_ok(c):
            e.append(f"interval.customize: {c} 不是卡（写 p_card-…）")
    if not isinstance(iv.get("allow_support", False), bool):
        e.append("interval.allow_support: 写 true / false")
    if (full.get("reroll") or {}).get("mode") not in REROLL_MODES:
        e.append(f"reroll.mode 应为 {'/'.join(REROLL_MODES)}")
    ex = full.get("exam") or {}
    if ex.get("maa") not in MAA_MODES:
        e.append(f"exam.maa 应为 {'/'.join(MAA_MODES)}")
    for where, t in [("exam.timeline", ex.get("timeline"))] + [(f"exam.timelines.{k}", v) for k, v in (ex.get("timelines") or {}).items()]:
        if where.startswith("exam.timelines.") and where.split(".", 2)[2] not in STAGES:
            e.append(f"{where}: 审查只能是 {'/'.join(STAGES)}")
        err = _timeline_error(t)
        if err:
            e.append(f"{where}: {err}")
    if ex.get("budget") not in EXAM_BUDGET:
        e.append(f"exam.budget: {' / '.join(EXAM_BUDGET)}")
    if ex.get("drinks") not in EXAM_DRINKS:
        e.append(f"exam.drinks: {' / '.join(EXAM_DRINKS)}")
    lines = (ex.get("retry") or {}).get("lines")
    if not isinstance(lines, dict) or not all(
            k in RETRY_STAGES and isinstance(v, int) and not isinstance(v, bool) and v >= 0 for k, v in lines.items()):
        e.append(f"exam.retry.lines: {{审查: 分数线}}（审查 {' / '.join(RETRY_STAGES)}；0 = 不重来）")
    skip = ex.get("skip")
    if not isinstance(skip, list) or not all(x in RETRY_STAGES for x in skip):
        e.append(f"exam.skip: 要跳过的审查的列表（{' / '.join(RETRY_STAGES)}）")
    sp = ex.get("split_preset")
    if sp is not None:
        from GakumasCLI.scenario import split_preset as SPL
        try:
            SPL.from_full_code(sp if isinstance(sp, str) else "")
        except SPL.PresetError as exc:
            e.append(f"exam.split_preset: {SPL.SHARE_PREFIX} 完整码（{'；'.join(exc.errors[:2])}）")
    if e:
        raise PlanError(e)
    return full


def _score_ok(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100


def _score_errors(cards):
    """M1: cards.scores / defaults / score_role / min_take / protect / convertible."""
    e = []
    sc = cards.get("scores") or {}
    if not isinstance(sc, dict):
        e.append("cards.scores: 写成 {卡 id（可带 +1）: 0–100}")
    else:
        for k, v in sc.items():
            if not _card_ok(k) or not _score_ok(v):
                e.append(f"cards.scores.{k}: 卡 id（可带 +1），分 0–100")
    df = cards.get("defaults") or {}
    for k, v in df.items():
        if k not in SCORE_DEFAULTS:
            e.append(f"cards.defaults.{k}: 只有 {' / '.join(SCORE_DEFAULTS)}")
        elif k in V_DEFAULTS:
            if not (v == "v" or _score_ok(v)):
                e.append(f'cards.defaults.{k}: "v"（按 V 估）或 0–100')
        elif not _score_ok(v):
            e.append(f"cards.defaults.{k}: 0–100")
    if cards.get("score_role", "auto") not in SCORE_ROLES:
        e.append(f"cards.score_role 应为 {'/'.join(SCORE_ROLES)}")
    if not _score_ok(cards.get("min_take", 0)):
        e.append("cards.min_take: 0–100（报价全低于它、又能不拿时不拿）")
    pr = cards.get("protect") or {}
    at = pr.get("at", 80)
    if not (isinstance(at, (int, float)) and not isinstance(at, bool) and 0 <= at <= 101):
        e.append("cards.protect.at: 保护线 0–100（101 = 不按分保护）")
    for lst in ("no_change", "no_delete"):
        for x in pr.get(lst) or ():
            if not _card_ok(x):
                e.append(f"cards.protect.{lst}: {x} 不是卡 id")
    for x in cards.get("convertible") or ():
        if not _card_ok(x):
            e.append(f"cards.convertible: {x} 不是卡 id")
    return e


def _target_errors(target):
    """cards.target: [[card key, count 1-3], ...]; the deck rules (重複不可 / 支援事件卡 / LEGEND) from the master."""
    if target in (None, []):
        return []
    if not isinstance(target, list):
        return ["cards.target: 写成 [[卡 id（可带 +1）, 张数], ...]"]
    e, rows = [], []
    for x in target:
        if not (isinstance(x, (list, tuple)) and len(x) == 2 and _card_ok(x[0]) and isinstance(x[1], int) and 1 <= x[1] <= 3):
            e.append(f"cards.target: {x} 要写成 [卡 id（可带 +1）, 1–3 张]")
        else:
            rows.append((x[0], x[1]))
    if len(rows) > 40:
        e.append("cards.target: 最多 40 种卡")
    if rows and not e:
        try:
            from GakumasCLI.scenario import memory_cost as MC
            e.extend("cards.target: " + x for x in MC.deck_errors(rows))
        except OSError:
            pass
    return e


def _goal_errors(goal):
    goal = goal or {}
    e = []
    if goal.get("kind") not in GOAL_KINDS:
        return [f"goal.kind 应为 {'/'.join(GOAL_KINDS)}"]
    if goal.get("grade") not in MEMORY_GRADES:
        e.append(f"goal.grade 应为 {'/'.join(MEMORY_GRADES)}")
    cards = goal.get("cards") or []
    if not isinstance(cards, list) or not all(_card_ok(c) for c in cards):
        e.append("goal.cards: 卡 id 的列表（可带 +1），偶像固有卡不算")
        return e
    if goal["kind"] == "memory":
        if not cards:
            e.append("goal.cards: 记忆目标要写想要的卡（最多 5 张，偶像固有卡不算）")
        elif not e:
            try:
                from GakumasCLI.scenario import memory_cost as MC
                e.extend("goal.cards: " + x for x in MC.check_target(cards, goal["grade"]))
            except OSError:
                if len(cards) > 5:
                    e.append("goal.cards: 最多 5 张")
    return e


def _timeline_error(t):
    if t is None:
        return None
    from GakumasCLI.scenario import timeline as TL
    try:
        if isinstance(t, str) and t.startswith(TL.SHARE_PREFIX):
            TL.from_share_code(t)
        elif isinstance(t, str):
            if t not in [p["id"] for p in TL.PRESETS] and not re.match(r"^tl_[0-9a-f]{12}$", t):
                return f"{t} 不是时间轴预设、库 id 或 {TL.SHARE_PREFIX} 分享码"
        elif isinstance(t, dict):
            TL.validate(t)
        else:
            return "写时间轴预设 id、库 id、分享码或整份时间轴文档"
    except Exception as exc:                                    # noqa: BLE001
        return f"时间轴无效: {exc}"
    return None


def zemi_on(full):
    """The 老师周 mode."""
    return (full or {}).get("mode") == "zemi"


def _exam_here(full, produce_id):
    """the plan's exam switches act on this produce's auditions -- every H.I.F audition in the normal mode, the 本戦's in
    the 老师周 mode (its 選抜 is the player's, as in v1)."""
    if produce_id not in ("produce-007", "produce-008"):
        return False
    return produce_id == "produce-008" or not zemi_on(full)


def _drinks_only(full, kind):
    return full["shop"]["buy"] == "drinks" and not (kind == "interval" and full["interval"]["customize"])


def rule_only(full, kind, produce_id=None, option_kinds=None):
    """the plan decides this decision by its own rule (a quick pick, a 老师周 幕間 with nothing listed), so the station starts
    no search for it (「能用规则定的地方不让搜索算」)."""
    if not full or (zemi_on(full) and produce_id == "produce-007"):
        return False
    if full["stops"].get(kind) == "first":
        return True
    if kind in ("shop", "interval") and _drinks_only(full, kind):
        return option_kinds is not None and "drink" not in option_kinds
    if kind == "interval" and zemi_on(full):
        return not full["interval"]["customize"]
    return False


def search_kinds(full, kind, produce_id=None):
    """the option kinds a drinks-only 相談 / 幕間 searches (the sold drinks, the held ones' discards), or None (every option,
    as before)."""
    if not full or (zemi_on(full) and produce_id == "produce-007") or kind not in ("shop", "interval"):
        return None
    return ("drink", "drop") if _drinks_only(full, kind) else None


def exam_round_options(full, produce_id):
    """The LiveRound budget an audition searches with under exam.budget "low" / "short" ({} otherwise)."""
    if not full or not _exam_here(full, produce_id) or full["exam"]["budget"] not in ("low", "short"):
        return {}
    return dict(LOW_BUDGET)


def exam_short(full, produce_id):
    """an audition under exam.budget "short" runs only the quick solves (the opening's 15 s, a deviation's 15 s
    re-solve), never the long opening or the deep re-solve."""
    return bool(full) and _exam_here(full, produce_id) and full["exam"]["budget"] == "short"


def exam_drinks_all(full, produce_id):
    """an audition under exam.drinks "all" opens without the drink table -- every drink held is the plan's (全喝), as the
    下半場 always was."""
    return bool(full) and _exam_here(full, produce_id) and full["exam"]["drinks"] == "all"


def _stage_here(full, stage):
    return stage in RETRY_STAGES and (not zemi_on(full) or stage.startswith("hif.main"))


def exam_skip(full, stage):
    """an audition listed in exam.skip is given up at its opening -- no search, every turn ended, no retry."""
    return bool(full) and _stage_here(full, stage) and stage in full["exam"]["skip"]


def retry_line(full, stage):
    """the score line of an audition (timeline.stage_key) under exam.retry.lines, or None (no line, a line of 0, a 選抜
    audition in the 老师周 mode)."""
    if not full or not _stage_here(full, stage):
        return None
    return int(full["exam"]["retry"]["lines"].get(stage) or 0) or None


def board_on(full):
    """the station puts ``produce_zemi`` on the board (the retry / give-up MAA reads) -- the 老师周 mode, or a plan with a
    retry line or a skipped audition."""
    if not full:
        return False
    ex = full["exam"]
    return zemi_on(full) or any(ex["retry"]["lines"].values()) or bool(ex["skip"])


def exam_timeline(full, stage):
    """The timeline for one audition (hif.sel1 .. hif.main2): the stage's own, else the plan's, else None."""
    ex = full.get("exam") or {}
    return (ex.get("timelines") or {}).get(stage) or (ex.get("timelines") or {}).get("*") or ex.get("timeline")


def summary(doc):
    """One line in Chinese: what the plan does by itself and where it stops."""
    full = resolve(doc)
    st = full["stops"]
    auto = [KINDS[k].split("（")[0] for k in KINDS if st.get(k) == "auto" and k not in ("story",)]
    first = [KINDS[k].split("（")[0] for k in KINDS if st.get(k) == "first"]
    sure = [KINDS[k].split("（")[0] for k in KINDS if st.get(k) == "sure"]
    stop = [KINDS[k].split("（")[0] for k in KINDS if st.get(k) == "stop" and k not in ("route",)]
    parts = [_goal_text(full), ROUTE_MODES[full["route"]["mode"]]]
    if full["cards"].get("target"):
        parts.append(f"目标卡组 {sum(n for _, n in full['cards']['target'])} 张")
    if full["route"].get("steps"):
        parts.append(f"{len(full['route']['steps'])} 步指定")
    if auto:
        parts.append("自动：" + "、".join(auto))
    if first:
        parts.append("拿第一个（不等搜索）：" + "、".join(first))
    if sure:
        parts.append("没把握才停：" + "、".join(sure))
    if stop:
        parts.append("停下：" + "、".join(stop))
    c = full["cards"]
    n = sum(len(c.get(k) or ()) for k in ("must", "avoid", "never")) + len(c.get("prefer") or {})
    if n:
        parts.append(f"卡牌偏好 {n} 条")
    if c.get("scores"):
        prot = (c.get("protect") or {}).get("at", 80)
        n_prot = len({k for k, v in c["scores"].items() if v >= prot} | set((c.get("protect") or {}).get("no_change") or ()))
        parts.append(f"打分 {len(c['scores'])} 张（受保护 {n_prot} 张）")
    if (full.get("formation") or {}).get("slot"):
        parts.append(f"回忆编成第 {full['formation']['slot']} 格")
    if full["events"]:
        parts.append(f"事件指定 {len(full['events'])} 个")
    t = exam_timeline(full, "*")
    if t or full["exam"].get("timelines"):
        parts.append("审查带时间轴")
    if zemi_on(full):
        parts.insert(1, "老师周本战（只走本战，选拔你来）")
    sh, ex = full["shop"], full["exam"]
    if sh["buy"] == "drinks":
        parts.append("咨询、幕间只买饮料（有空位就买，满了算过再换）" if sh["drinks"] == "always"
                     else "咨询、幕间只买饮料（算过值才买、再丢）")
    elif sh["buy"] in ("must_only", "never"):
        parts.append("商店" + SHOP_BUY[sh["buy"]])
    if sh["buy"] == "drinks" and sh.get("refresh"):
        parts.append(f"没饮料可买时最多刷新 {sh['refresh']} 次")
    if sh["delete"] in GAINED_DELETES:
        what = {"gained": "本战获得的卡", "gained_normal": "本战获得的常规卡", "gained_support": "本战支援卡发的卡"}
        parts.append(f"咨询删{what[sh['delete']]}" + ("（先刷新）" if sh["refresh_for_delete"] else ""))
    ct = {"first_school": "变掉本战第一次授课换来的卡", "advice": "按推荐", "gained": "变掉本战获得的卡",
          "named": "变掉指定的卡"}
    c1, c2 = full["cards"]["change_target"], full["cards"].get("change_target_2", "same")
    if c1 in ct:
        parts.append("变卡" + ct[c1])
    if c2 != "same" and c2 != c1:
        parts.append("第二次起" + (ct.get(c2) or CHANGE_TARGETS[c2]))
    if full.get("memory") == "stop":
        parts.append("回忆卡停下等你")
    prefs = [f"{w}属性 {ATTR_PREFS[v]}" for w, v in (("授業", full["school"].get("attr")),
                                                     ("训练", full["route"].get("lesson_attr"))) if v in ATTRS]
    if prefs:
        parts.append("、".join(prefs))
    exam = [EXAM_BUDGET[ex["budget"]]] if ex["budget"] != "normal" else []
    if ex["drinks"] == "all":
        exam.append("饮料全喝")
    if exam:
        parts.append("审查" + "、".join(exam))
    lines = [ex["retry"]["lines"][st] for st in RETRY_STAGES if ex["retry"]["lines"].get(st)]
    if lines:
        parts.append("打不到 " + " / ".join(f"{x / 10000:g}w" for x in lines) + " 用重来券")
    if "hif.main2" in ex["skip"]:
        parts.append("下半场直接跳过（每回合结束）")
    if ex.get("split_preset"):
        try:
            from GakumasCLI.scenario import split_preset as SPL
            parts.append(f"带控分预设「{SPL.from_full_code(ex['split_preset'])['name']}」")
        except Exception:                                   # noqa: BLE001
            parts.append("带控分预设")
    return "；".join(parts)


def _goal_text(full):
    g = full.get("goal") or {}
    if g.get("kind") != "memory":
        return "目标：H.I.F 高分"
    try:
        from GakumasCLI.scenario import memory_cost as MC
        lower, upper = MC.box(g["grade"])
        return f"目标：记忆 {g['grade']}（{len(g['cards'])} 张，占容 {MC.target_cost(g['cards'])}/{upper or '?'}）"
    except (OSError, ValueError):
        return f"目标：记忆 {g.get('grade')}（{len(g.get('cards') or ())} 张）"


# ------------------------------------------------------------------ deciding
def _card_of(o):
    c = o.get("card")
    if isinstance(c, (list, tuple)) and c:
        return f"{c[0]}+{int(c[1]) if len(c) > 1 else 0}"
    return c if isinstance(c, str) else None


def _matches(key, lst):
    base = key.split("+")[0] if key else None
    return bool(key) and (key in lst or base in lst)


def score_tier(score):
    """M1: the display tier of a 0-100 score (S >= 90, A >= 80, B >= 60, else C); decisions use the number."""
    for name, low in SCORE_TIERS:
        if score >= low:
            return name
    return "C"


def _classes():
    try:
        from GakumasCLI.scenario import memory_cost as MC
        return MC.card_classes()
    except (OSError, ValueError):
        return {}


def card_score(full, key, v_score=None):
    """M1: the 0-100 score of card KEY ("<id>+<upgrade>"), or None when nothing scores it."""
    if not key:
        return None
    c = full["cards"]
    sc = c.get("scores") or {}
    if key in sc:
        return sc[key]
    base = key.split("+")[0]
    if base in sc:
        return sc[base]
    df = dict(SCORE_DEFAULTS, **(c.get("defaults") or {}))
    if base in (c.get("convertible") or ()):
        return df["convertible"] if df["convertible"] != "v" else v_score
    rarity, category = _classes().get(base, (None, None))
    if category == "TROUBLE":
        return df["trouble"]
    if rarity == "R" and "-ido-" not in base:
        return df["r"] if df["r"] != "v" else v_score
    if v_score is not None:
        return v_score
    return df["unlisted"] if df["unlisted"] != "v" else None


def protection(full, key, what, deck=None):
    """M1: why card KEY may not be given up by a change (WHAT "change") or deleted ("delete"), or None."""
    c = full["cards"]
    pr = c.get("protect") or {}
    base = key.split("+")[0]
    at = pr.get("at", 80)
    s = card_score(full, key)
    if s is not None and s >= at:
        return f"分 {s:g} ≥ 保护线 {at:g}"
    if _matches(key, pr.get("no_change") or ()):
        return "你设成禁止变"
    if what == "delete" and _matches(key, pr.get("no_delete") or ()):
        return "你设成禁止删"
    if what == "delete" and base in (c.get("convertible") or ()):
        return "局外可转化的卡不删"
    want = {}
    for k, n in c.get("target") or ():
        want[k.split("+")[0]] = want.get(k.split("+")[0], 0) + int(n)
    if base in want:
        have = sum(1 for k in deck or () if str(k).split("+")[0] == base) if deck is not None else 0
        if have <= want[base]:
            return "目标卡组里的卡"
    goal = full.get("goal") or {}
    if "-ido-" in base and goal.get("unique") in (None, base):
        return "偶像固有卡"
    if goal.get("kind") == "memory" and "-sup-" in base:
        return "记忆目标下的支援卡技能卡（回忆第 1 位）"
    return None


def give_up(full, d, what="change"):
    """equal scores: lower V ("v_deck"), not upgraded, then the station's order."""
    rows = []
    vd = d.get("v_deck") or {}
    vs = d.get("v_score_deck") or {}
    v_only = bool(vd) and not full["cards"].get("scores")
    for i, key in enumerate(d.get("removable") or d.get("deck") or ()):
        key = str(key)
        if protection(full, key, what, d.get("deck")):
            continue
        sc = vs.get(key) if v_only else card_score(full, key, vs.get(key))
        up = int(key.split("+")[1]) if "+" in key and key.split("+")[1].isdigit() else 0
        rows.append(((sc if sc is not None else 50), vd.get(key, 0), up, i, key))
    return min(rows)[-1] if rows else None


def formation_check(full, data):
    """the produce's memories against the plan's memory deck (formation.slot, 1-20)."""
    slot = ((full or {}).get("formation") or {}).get("slot")
    used = sorted(m.get("user_memory_id") for m in (data or {}).get("user_produce_progress_memory_list") or ()
                  if m.get("user_memory_id"))
    if not slot or not used:
        return None
    decks = [x for x in (data or {}).get("user_produce_memory_deck_list") or () if x.get("user_memory_ids")]
    match = sorted({x.get("number") for x in decks if sorted(x.get("user_memory_ids") or ()) == used})
    mine = [x for x in decks if x.get("number") == slot]
    ok = None if not mine else any(sorted(x.get("user_memory_ids") or ()) == used for x in mine)
    return {"slot": slot, "ok": ok, "used": match}


SERVICE_FIELD = {"delete": "delete", "change": "give_up", "upgrade": "upgrade"}
SERVICE_VERB = {"delete": "删", "change": "变", "upgrade": "强化"}


def service_card(full, svc, d):
    """the deck card a shop / interval service works on."""
    cards = [str(k) for k in svc.get("cards") or ()]
    if svc.get("action") in ("delete", "change"):
        return give_up(full, {"removable": cards, "v_deck": svc.get("v") or {}, "v_score_deck": svc.get("v_score") or {},
                              "deck": d.get("deck")}, what=svc["action"])
    goal = full.get("goal") or {}
    keep0 = {k.split("+")[0] for k in goal.get("cards") or () if k.endswith("+0")} if goal.get("kind") == "memory" \
        else set()
    vs = svc.get("v_score") or {}
    v_only = bool(vs) and not full["cards"].get("scores")
    up = [((vs.get(k) if v_only else card_score(full, k, vs.get(k))), -i, k) for i, k in enumerate(cards)
          if k.endswith("+0") and k.split("+")[0] not in keep0]
    up = [x for x in up if x[0] is not None]
    return max(up)[-1] if up else None


def _guards(full):
    c = full["cards"]
    pr = c.get("protect") or {}
    return bool(c.get("scores") or pr.get("no_change") or pr.get("no_delete") or c.get("convertible"))


def _services(full, d, res):
    """a shop / interval answer that is a delete / change / upgrade names its card; one whose every card is protected
    gives way to the next option the search rates (or stops)."""
    svc = d.get("services") or {}
    oid = res.get("option")
    if res.get("mode") != "auto" or oid not in svc:
        return res
    s = svc[oid]
    key = service_card(full, s, d)
    if key is not None or s.get("action") == "upgrade":
        if key is not None:
            res[SERVICE_FIELD[s["action"]]] = key
        return res
    verb = SERVICE_VERB[s["action"]]
    for o in sorted((o for o in d.get("options") or () if o["id"] != oid and o.get("rating") is not None),
                    key=lambda o: -o["rating"]):
        alt = svc.get(o["id"])
        alt_key = service_card(full, alt, d) if alt else None
        if alt and alt_key is None and alt.get("action") != "upgrade":
            continue
        out = {"mode": "auto", "option": o["id"], "why": f"建议要{verb}的卡都在保护区，改选下一个：{o.get('label') or o['id']}"}
        if alt and alt_key is not None:
            out[SERVICE_FIELD[alt["action"]]] = alt_key
        return out
    return {"mode": "stop", "option": None, "why": f"建议要{verb}的卡都在保护区，等你选"}


def _score_role(full, d=None):
    """The scores' place against the advice."""
    role = full["cards"].get("score_role", "auto")
    if role == "auto":
        if (full.get("goal") or {}).get("kind") == "memory":
            return "lead"
        if _v_decides(d):
            return "lead" if full["cards"].get("scores") else "off"
        return "tiebreak"
    return role


def _v_decides(d):
    """the station's input carries a pick pack's V (pickv.maa_decision)."""
    return bool(d) and (d.get("advice") or {}).get("source") == "v" and bool(d.get("v"))


def _v_separated(opts, v):
    """V's best option and whether it leads the next by V_TIE (the "tiebreak" role)."""
    from GakumasCLI.produce.pickv import V_TIE
    known = sorted((v[o["id"]] for o in opts if o["id"] in v), reverse=True)
    return len(known) >= 2 and known[0] - known[1] >= V_TIE


def _score_pick(full, opts, d, adv):
    """M1: a card decision by the player's scores, or None (V and the search decide)."""
    role = _score_role(full, d)
    if role == "off":
        return None
    if role == "tiebreak":
        if _v_decides(d):
            if _v_separated(opts, d.get("v") or {}):
                return None
        elif adv.get("separated") and adv.get("lead") in [o["id"] for o in opts]:
            return None
    if all(card_score(full, _card_of(o)) is None for o in opts):
        return None
    vs = d.get("v_score") or {}
    known = {o["id"]: card_score(full, _card_of(o), vs.get(o["id"])) for o in opts}
    known = {k: v for k, v in known.items() if v is not None}
    best = max(known.values())
    if len(known) < len(opts) and best < (full["cards"].get("protect") or {}).get("at", 80):
        return None
    min_take = full["cards"].get("min_take", 0)
    if best < min_take and len(known) == len(opts) and d.get("skip_id"):
        return {"mode": "auto", "option": d["skip_id"], "why": f"报价最高 {best:g} 分，低于最低要拿的 {min_take:g}，不拿"}
    v = d.get("v") or {}
    top = [o for o in opts if o["id"] in known and best - known[o["id"]] < TIE_MARGIN]
    choice = max(top, key=lambda o: (o["id"] in v, v.get(o["id"]) or 0, o.get("rating") is not None,
                                     o.get("rating") or 0, known[o["id"]]))
    sc = known[choice["id"]]
    why = f"按你的打分：{choice.get('label') or _card_of(choice)} {sc:g} 分（{score_tier(sc)}）"
    if len(top) > 1:
        why += f"；另有 {len(top) - 1} 张差不到 {TIE_MARGIN} 分，按 V / 搜索定"
    return {"mode": "auto", "option": choice["id"], "why": why}


def _customize_pick(full, opts, d):
    """M1: a delete (d["action"] "delete": the lowest-scored option nothing protects) or an upgrade ("upgrade": the
    highest-scored card not yet upgraded; under a memory goal not one whose +0 the goal names).
    """
    vs = d.get("v_score") or {}
    rows = [(card_score(full, _card_of(o), vs.get(o["id"])), i, o) for i, o in enumerate(opts) if _card_of(o)]
    if not any(s is not None for s, _, _ in rows):
        return None
    label = lambda o: o.get("label") or _card_of(o)                               # noqa: E731
    if d.get("action") == "delete":
        free = [((s if s is not None else 50), i, o) for s, i, o in rows
                if not protection(full, _card_of(o), "delete", d.get("deck"))]
        if not free:
            return {"mode": "stop", "option": None, "why": "能删的卡都在保护区"}
        s, _, o = min(free)
        return {"mode": "auto", "option": o["id"], "why": f"删分最低、不受保护的：{label(o)} {s:g} 分"}
    keep0 = {k.split("+")[0] for k in (full.get("goal") or {}).get("cards") or () if k.endswith("+0")} \
        if (full.get("goal") or {}).get("kind") == "memory" else set()
    up = [(s, -i, o) for s, i, o in rows if s is not None and _card_of(o).endswith("+0")
          and _card_of(o).split("+")[0] not in keep0]
    if not up:
        return None
    s, _, o = max(up)
    return {"mode": "auto", "option": o["id"], "why": f"强化分最高、还没强化的：{label(o)} {s:g} 分"}


def decide(full, d):
    """One produce decision."""
    res = _decide(full, d)
    c = full["cards"]
    if (d.get("kind") == "card_change" and (d.get("removable") or d.get("deck"))
            and (c.get("scores") or (c.get("protect") or {}).get("no_change") or d.get("v_deck"))):
        res["give_up"] = give_up(full, d)
    if d.get("kind") in ("shop", "interval") and d.get("services") and (_guards(full) or _v_decides(d)):
        res = _services(full, d, res)
    res = _plan_services(full, d, res)
    if d.get("kind") == "drink_pick" and res.get("mode") == "auto":
        keep = drink_keep(d, res.get("option"))
        if keep is not None:
            res["drink_keep"] = keep
    return res


def _decide(full, d):
    kind = d.get("kind")
    opts = list(d.get("options") or ())
    ids = [o["id"] for o in opts]
    adv = d.get("advice") or {}
    wait = full["wait_seconds"]
    crit = full["route"]["criteria"]
    stop = lambda why: {"mode": "stop", "option": None, "why": why}          # noqa: E731
    auto = lambda oid, why: {"mode": "auto", "option": oid, "why": why}      # noqa: E731
    if kind not in KINDS:
        return stop(f"不认识的决策：{kind}")
    answer = _plan_rules(full, d, opts, ids, stop, auto)
    if answer is not None:
        return answer
    if not opts:
        return stop("没有选项")
    mode = full["stops"].get(kind, "stop")
    if mode == "first":
        mode = "auto"

    if kind == "route":
        rm = full["route"]["mode"]
        if rm == "stop_each":
            return stop("路线：每步都停")
        if d.get("triangle") and crit == "avoid":
            n = lambda o: o.get("triangles", 1 if o.get("triangle") else 0)        # noqa: E731
            rate = lambda o: o["rating_clear"] if o.get("rating_clear") is not None else o["rating"]   # noqa: E731
            lead_n = int(d.get("lead_triangles") or 0) if d.get("criteria") else (d.get("lead_triangles") or 1)
            fewer = sorted((o for o in opts if o.get("rating") is not None and n(o) < lead_n
                            and o["id"] != d.get("advice", {}).get("lead")), key=lambda o: (n(o), -rate(o)))
            if fewer:
                pick = fewer[0]
                label = ROUTE_CHOICES.get(pick["id"], pick["id"])
                if d.get("criteria"):
                    return auto(pick["id"], "推荐路线到审查时有 %d 项参数不到审查基准，改走「%s」（%s）" % (
                        lead_n, label, "三项都能达到" if not n(pick) else "%d 项" % n(pick)))
                return auto(pick["id"], "推荐路线后面有 △，改走没有 △ 的「%s」" % label if not n(pick) else
                            "推荐路线后面有 %d 个 △，改走 △ 少的「%s」（%d 个）" % (lead_n, label, n(pick)))
        if d.get("triangle") and crit == "stop":
            return stop("推荐路线后面有 △（参数低于审查基准）")
        avoid = list(full["route"].get("avoid") or ())
        avoided = lambda oid: any(route_match(a, oid) for a in avoid)          # noqa: E731
        lead = adv.get("lead")
        if lead is not None and avoided(lead):
            ranked = sorted((o for o in opts if not avoided(o["id"]) and o.get("rating") is not None),
                            key=lambda o: -o["rating"])
            lead = ranked[0]["id"] if ranked else None
        if lead is None:
            return {"mode": "wait", "option": None, "why": "日程卡还在算"} if (adv.get("age") or 0) < wait \
                else stop("日程卡没算出来")
        pref = _attr_pref(full, opts, lead, avoided)
        if pref is not None:
            return auto(*pref)
        if d.get("triangle") and crit == "avoid":
            if d.get("criteria"):
                return auto(lead, "按日程卡（到审查时有 %d 项参数不到审查基准，别的选项不比它少）" % (d.get("lead_triangles") or 0))
            return auto(lead, "按日程卡（后面有 △，别的选项 △ 不比它少）")
        return auto(lead, "按日程卡")
    if kind in ("activity_event", "school_event"):
        key = d.get("event")
        for k, v in full["events"].items():
            if key and (key == k or key.startswith(k)):
                return auto(ids[v], f"事件指定选项 {v + 1}") if v < len(ids) else stop(f"事件指定的选项 {v + 1} 不存在")
        if kind == "school_event":
            tag = full["school"].get("tag")
            if tag not in (None, "advice"):
                hit = [o["id"] for o in opts if o.get("tag") == tag]
                if hit:
                    return auto(hit[0], f"授業标签指定：{tag}")
            attr = full["school"].get("attr")
            if attr in ATTRS:
                hit = [o["id"] for o in opts if o.get("attr") == attr]
                if hit:
                    return auto(hit[0], f"授業属性指定：{attr}")
    if kind == "reroll":
        rmode = full["reroll"]["mode"]
        if rmode == "never":
            return auto(d.get("skip_id") or "skip", "不重抽")
        if rmode == "if_no_target":
            targets = set(d.get("targets") or ())
            if any(_card_of(o) in targets for o in opts):
                return auto(d.get("skip_id") or "skip", "报价里有目标卡，不重抽")
            return auto(d.get("reroll_id") or "reroll", "报价里没有目标卡，重抽")
        if rmode == "by_v":
            g = d.get("v_reroll")
            if not g:
                return stop("重抽：没有选卡包，V 算不了，停下等你")
            if g.get("worth"):
                return auto(d.get("reroll_id") or "reroll", "按选卡包 V：新报价更可能有好卡，重抽")
            return auto(d.get("skip_id") or "skip", "按选卡包 V：现在的报价不比重抽差，不重抽")
        return stop("重抽 / 除外：停下等你")
    if kind in ("card_pick", "card_change"):
        cards = full["cards"]
        allowed = [o for o in opts if not _matches(_card_of(o), cards.get("never") or ())]
        must = [o for o in allowed if _matches(_card_of(o), cards.get("must") or ())]
        if must:
            return auto(must[0]["id"], "必拿的卡")
        lacking = _target_lacking(cards.get("target"), d.get("deck"))
        hits = [o for o in allowed if _card_of(o) and _card_of(o).split("+")[0] in lacking]
        if hits:
            best = max(hits, key=lambda o: ((d.get("v") or {}).get(o["id"], 0), o.get("rating") or 0))
            return auto(best["id"], "目标卡组里还缺的卡")
        goal = full.get("goal") or {}
        if goal.get("kind") == "memory" and goal.get("cards") and d.get("deck") is not None:
            answer = _memory_pick(allowed, d["deck"], goal, d.get("held") or ())
            if answer is not None:
                return auto(*answer)
        if mode == "stop":
            return stop(f"{KINDS[kind]}：停下等你")
        if not allowed:
            return stop("报价全是你设成「不要」的卡")
        rerolled = _card_reroll(full, d, cards, lacking)
        if rerolled is not None:
            return rerolled
        scored = _score_pick(full, allowed, d, adv)
        if scored is not None:
            return scored
        if _v_decides(d) and d.get("v_skip") and d.get("skip_id") is not None and not cards.get("scores"):
            return auto(d["skip_id"], "按选卡包 V：报价都低于下限，不拿")
        if _v_decides(d) and adv.get("tie") == "pending" and (adv.get("age") or 0) < wait:
            return {"mode": "wait", "option": None, "why": "选卡包 V 前两名接近，等模拟分出高下"}
        ranked = _rank_cards(allowed, adv, (d.get("v_rank") if _v_decides(d) else None) or d.get("v") or {}, cards)
        if ranked is None:
            return {"mode": "wait", "option": None, "why": "建议还在算"} if (adv.get("age") or 0) < wait \
                else stop("建议没算出来，V 也不认识这些卡")
        oid, sure, why = ranked
        if mode == "sure" and not sure:
            return stop("没把握：" + why)
        return auto(oid, why)
    if kind in ("drink_pick", "drink_discard"):
        dr = full["drinks"]
        res = lambda o: (_card_of(o) or "").split("+")[0]          # noqa: E731
        if kind == "drink_pick":
            hit = [o for o in opts if res(o) in (dr.get("keep") or ())]
            if hit:
                return auto(hit[0]["id"], "你要的饮料")
        else:
            hit = [o for o in opts if res(o) in (dr.get("discard_first") or ())]
            if hit:
                return auto(hit[0]["id"], "你设成先丢的饮料")
            spare = [o for o in opts if res(o) not in (dr.get("keep") or ())]
            if dr.get("keep") and len(spare) < len(opts):
                if len(spare) == 1:
                    return auto(spare[0]["id"], "留下你要的饮料，丢这瓶")
                if adv.get("lead") not in [o["id"] for o in spare] and mode != "stop":
                    return stop("建议要丢你设成要留的饮料")
    if kind == "shop" and full["shop"]["buy"] != "never":
        cards = full["cards"]
        lacking = _target_lacking(cards.get("target"), d.get("deck"))
        hit = [o for o in opts if _card_of(o) and (_matches(_card_of(o), cards.get("must") or ())
                                                   or _card_of(o).split("+")[0] in lacking)
               and not _matches(_card_of(o), cards.get("never") or ())]
        if hit:
            return auto(hit[0]["id"], "商店里有必拿 / 目标卡组还缺的卡")
        if full["shop"]["buy"] == "advice":
            at = (cards.get("protect") or {}).get("at", 80)
            good = [(card_score(full, _card_of(o)), o) for o in opts if _card_of(o)
                    and not _matches(_card_of(o), cards.get("never") or ())]
            good = [(sc, o) for sc, o in good if sc is not None and sc >= at]
            if good:
                sc, o = max(good, key=lambda x: x[0])
                return auto(o["id"], f"商店里有你打了 {sc:g} 分的卡")
    if (kind in ("shop", "interval") and _v_decides(d) and full["shop"]["buy"] not in ("never", "must_only")
            and mode != "stop"):
        answer = _v_shop(full, d, opts)
        if answer is not None:
            return auto(*answer)
    if kind == "customize" and d.get("action") in ("delete", "upgrade") and mode != "stop":
        answer = _customize_pick(full, opts, d)
        if answer is not None:
            return answer
    if mode == "stop":
        return stop(f"{KINDS[kind]}：停下等你")
    lead = adv.get("lead")
    if kind == "shop" and full["shop"]["buy"] in ("never", "must_only"):
        lead = lead if lead in [o["id"] for o in opts if not o.get("card")] else (d.get("leave_id") or None)
    if lead is None:
        if not adv.get("ready") and (adv.get("age") or 0) < wait:
            return {"mode": "wait", "option": None, "why": "建议还在算"}
        return stop("建议没算出来")
    if (kind == "drink_pick" and len(d.get("drinks_held") or ()) >= DRINK_SLOTS and not adv.get("separated")
            and (adv.get("age") or 0) < wait):
        return {"mode": "wait", "option": None, "why": "饮料满了：等搜索算清留哪 4 瓶"}
    if mode == "sure" and not adv.get("separated"):
        return stop("没把握：建议没分出胜负")
    return auto(lead, "按建议")


def _shop_item(o, d):
    """an option's SHOP_ITEMS name (a service by its action, a card, a drink, stamina)."""
    svc = (d.get("services") or {}).get(o["id"])
    if svc is not None:
        return svc.get("action")
    if _is_drink(o):
        return "drink"
    if o.get("card"):
        return "card"
    return "stamina" if str(o.get("label") or "").startswith("stamina") else None


def _v_shop(full, d, opts):
    """a shop / 幕間 visit with a pick pack's V, after the plan's must / target / protected rules: what is worth buying --
      card     V_{n+1} >= TAU          upgrade  V_1 x u >= TAU        change  V(in) - V(out) > 0
      delete   the lowest held V_n < TAU (the card the plan gives up, give_up)
      drink / stamina  the search's own rating > 0 (drinks stay as before)
    -- within the P points above shop.reserve_p, in shop.order's item order when the plan sets one (it had been stored
    but read by nothing), else by value; nothing worth it: leave (结束).
    """
    from GakumasCLI.produce.pickv import TAU
    v = d.get("v") or {}
    p = d.get("p_points")
    room = None if p is None else p - int(full["shop"].get("reserve_p") or 0)
    order = list(full["shop"].get("order") or ())
    worth = []
    for o in opts:
        item = _shop_item(o, d)
        price = o.get("price")
        if room is not None and price is not None and price > room:
            continue
        if item in ("card", "upgrade") and o["id"] in v and v[o["id"]] >= TAU:
            worth.append((item, v[o["id"]], o, None))
        elif item == "change" and o["id"] in v and v[o["id"]] > 0:
            worth.append((item, v[o["id"]], o, "换进的比换出的常用"))
        elif item == "delete":
            low = min(((x, k) for k, x in ((d.get("services") or {}).get(o["id"]) or {}).get("v", {}).items()),
                      default=None)
            if low is not None and low[0] < TAU:
                worth.append((item, TAU - low[0], o, "删前段记录里很少带的卡"))
        elif item in ("drink", "stamina") and (o.get("rating") or 0) > 0:
            worth.append((item, o["rating"] / 1e6, o, f"模拟 {o['rating']:+,.0f}"))
    if worth:
        rank = lambda w: (order.index(w[0]) if w[0] in order else len(order), -w[1])   # noqa: E731
        item, _val, o, why = min(worth, key=rank)
        what = {"card": "卡", "upgrade": "强化", "change": "换卡", "delete": "删卡", "drink": "饮料", "stamina": "体力"}[item]
        return o["id"], f"按选卡包 V 买{what}：{o.get('label') or o['id']}" + (f"（{why}）" if why else "")
    if d.get("leave_id") is not None:
        return d["leave_id"], "按选卡包 V：没有值得买的，离开"
    return None


_ATTR_FAMILY = re.compile(r"^(event_school|lesson|open_lesson|self_lesson|legend_lesson)_(vo|da|vi)(?:_|$)")


def _attr_pref(full, opts, lead, avoided=lambda oid: False):
    """(option, why) when the schedule card recommends a 授業 / a lesson of another attribute than the plan prefers
    (school.attr / route.lesson_attr) and this step offers the same kind in the preferred one -- SP / ★ first, never a
    hard one first (route_pick) -- else None."""
    m = _ATTR_FAMILY.match(str(lead or ""))
    if not m:
        return None
    family, attr = m.groups()
    want = full["school"].get("attr") if family == "event_school" else full["route"].get("lesson_attr", "route")
    if want not in ATTRS or want == attr:
        return None
    same = [o["id"] for o in opts if not avoided(o["id"]) and (lambda x: x is not None and x.groups() == (family, want))(
        _ATTR_FAMILY.match(str(o["id"])))]
    if not same:
        return None
    pick = route_pick(want if family != "event_school" else f"school_{want}", same) or same[0]
    what = "授業" if family == "event_school" else "训练"
    return pick, f"按日程卡走{what}，属性按方案：{ATTR_PREFS[want]}（日程卡推荐的是 {attr.capitalize()}）"


def _is_drink(o):
    return bool(o.get("drink")) or str(o.get("card") or "").startswith("pdrink")


def _plan_rules(full, d, opts, ids, stop, auto):
    """The plan's own answers before the search's (None = the rest of _decide): the 老师周 mode leaves a 選抜 to the
    player; a step held in route.steps (never a candidate) a quick pick ("first", v1's zemi.fast_pick); a 相談
    that deletes the 本戦's own card (shop.delete gained); a drinks-only 相談 / 幕間 (_drinks_shop); a 老师周 幕間 with
    nothing listed is left.
    """
    z = zemi_on(full)
    tag = "老师周：" if z else ""
    kind = d.get("kind")
    if z and d.get("produce") == "produce-007":
        return stop("老师周模式只走本战：選抜你来")
    if kind == "route":
        held = full["route"]["steps"].get(f"{str(d.get('produce', ''))[-3:]}:{d.get('step')}")
        if held is None:
            return None
        hit = route_pick(held, ids) if ids else None
        if hit is None and held == "rest":
            return auto("rest", f"{'老师周路线' if z else '指定的这一步'}：休息")
        if hit is None:
            return stop(f"{'老师周路线' if z else '指定'}的「{ROUTE_CHOICES.get(held, held)}」不在这一步的候选里")
        return auto(hit, "老师周路线" if z else "开局锁定 / 指定的这一步")
    if not opts:
        return None
    if full["stops"].get(kind) == "first" and kind in FIRST_KINDS and not (
            kind == "card_change" and full["cards"]["change_target"] == "advice"):
        what = {"card_pick": "拿第一张", "card_change": "换来第一张", "drink_pick": "拿第一瓶", "drink_discard": "丢第一瓶"}
        gone = _removed_ids(d) if kind in ("card_pick", "card_change") else set()
        fresh = [o for o in opts if (_card_of(o) or "").split("+")[0] not in gone]
        if fresh and fresh[0] is not opts[0]:
            return auto(fresh[0]["id"], f"{tag}快速选择，{what[kind]}（第一张是本局换掉 / 删掉过的卡，跳过）")
        return auto(opts[0]["id"], f"{tag}快速选择，{what[kind]}")
    if kind == "shop" and full["shop"]["delete"] in GAINED_DELETES:
        hit = _gained_delete(d, full["shop"]["delete"])
        if hit is not None:
            what = {"gained": "本战中获得的卡", "gained_normal": "本战中获得的常规卡", "gained_support": "支援卡发的卡"}
            return auto(hit[0], f"{tag}删{what[full['shop']['delete']]}")
    if kind in ("shop", "interval") and _drinks_only(full, kind):
        return _drinks_shop(full, d, opts, stop, auto, tag)
    if kind == "interval" and z and not full["interval"]["customize"] and d.get("leave_id") is not None:
        return auto(d["leave_id"], "老师周：幕间你没加要做的，不做")
    return None


DRINK_SLOTS = 4


def _removed_ids(d):
    """The card ids this produce's deck held and no longer does."""
    now = {str(k).split("+")[0] for k in d.get("deck") or ()}
    return {str(k).split("+")[0] for k in d.get("held") or ()} - now


def _drinks_shop(full, d, opts, stop, auto, tag=""):
    """a drinks-only 相談 / 幕間 buys the sold drink the search rates best when its rating is above 0; with four held, its
    rating is paired with the held drink whose discard loses least (hif_advice.Tally: ``with_drop``) and the answer
    names that one (``discard``, ``discard_slot``: its place in the bottom bar).
    """
    p = d.get("p_points")
    if p is not None:
        p = p - int(full["shop"].get("reserve_p") or 0)
    leave = d.get("leave_id")
    out = lambda why: auto(leave, why) if leave is not None else stop(why + "（没有离开的选项）")   # noqa: E731
    held = list(d.get("drinks_held") or ())
    full_now = len(held) >= DRINK_SLOTS
    drinks = [o for o in opts if _is_drink(o) and (p is None or o.get("price") is None or o["price"] <= p)]

    def leave_or_refresh(why):
        limit, used = int(full["shop"].get("refresh") or 0), int(d.get("rerolls") or 0)
        if not full_now and used < limit and (p is None or p >= REFRESH_COST + REFRESH_MIN_DRINK):
            return auto("refresh", f"{tag}{why}，刷新找饮料（第 {used + 1}/{limit} 次）")
        return out(f"{tag}{why}，离开" if not why.endswith("不买") else f"{tag}{why}")
    if not drinks:
        return leave_or_refresh("没有买得起的饮料")
    adv = d.get("advice") or {}
    age = adv.get("age") or 0
    rated = [o for o in drinks if o.get("rating") is not None]
    settle = full["wait_seconds"]
    always = full["shop"].get("drinks", "always") == "always"
    if not rated or (not adv.get("separated") and age < settle):
        if age < 3 * settle:
            return {"mode": "wait", "option": None, "why": f"{tag}饮料还在算值不值"}
        if not rated:
            if full_now:
                return out(f"{tag}饮料满了，没算出该丢哪瓶，不买")
            return auto(drinks[0]["id"], f"{tag}饮料没算出来，买第一瓶（有空位，不用丢）")
    best = max(rated, key=lambda o: o["rating"])
    if always and not full_now:
        return auto(best["id"], (tag + "有空位，买算下来最好的饮料（%+.0f；饮料会触发支援卡）") % best["rating"])
    if best["rating"] < 0 if always else best["rating"] <= 0:
        if not full_now:
            return leave_or_refresh("卖的饮料算下来都不值（最好的 %+.0f），不买" % best["rating"])
        return out((tag + ("饮料满了，换哪瓶都亏（最好的 %+.0f），不换" if always and full_now
                           else "卖的饮料算下来都不值（最好的 %+.0f），不买")) % best["rating"])
    if not full_now:
        return auto(best["id"], (tag + "买算下来最值的饮料（%+.0f）") % best["rating"])
    drop = best.get("with_drop")
    if drop not in held:
        return out(f"{tag}饮料满了，没算出该丢哪瓶，不买")
    res = auto(best["id"], (tag + "买算下来最值的饮料，丢掉最不值的那瓶（合计 %+.0f）") % best["rating"])
    res.update(discard=drop, discard_slot=held.index(drop))
    return res


def drink_keep(d, option):
    """the keep-drinks screen's worths for a drink pick with four held (MAA keeps the four worth most): {"gained": [worth
    of each offered drink, in the options' order], "chosen": the picked one's place, "held": [worth of each held
    drink, bottom-bar order]} -- an offered drink's own rating, a held one's minus its discard's (catch_v.maa_decision
    ``drink_values``); None when a worth is missing."""
    held = list(d.get("drinks_held") or ())
    vals = d.get("drink_values") or {}
    ids = [o["id"] for o in d.get("options") or ()]
    if len(held) < DRINK_SLOTS or option not in ids:
        return None
    gained = [(vals.get("gained") or {}).get(i) for i in ids]
    worth = [(vals.get("held") or {}).get(x) for x in held]
    if gained[ids.index(option)] is None or any(v is None for v in worth):
        return None
    return {"gained": gained, "chosen": ids.index(option), "held": worth}


def _gained(d, which="gained"):
    """The 本戦's gained cards the deck still holds, in the order they came (hif_advice.zemi_marks); WHICH gained_normal
    leaves out the ones a support card gave (``honsen_support``), gained_support keeps only those."""
    deck = set(d.get("deck") or ())
    support = set(d.get("honsen_support") or ())
    gained = [k for k in d.get("honsen_gained") or () if k in deck]
    if which == "gained_normal":
        return [k for k in gained if k not in support]
    if which == "gained_support":
        return [k for k in gained if k in support]
    return gained


def _gained_delete(d, which="gained"):
    """(the 相談 delete option, the 本戦 card it deletes) or None."""
    gained = _gained(d, which)
    for oid, s in (d.get("services") or {}).items():
        if s.get("action") == "delete" and gained:
            return oid, gained[0]
    return None


def _plan_services(full, d, res):
    """The card a change gives up under cards.change_target, and the 本戦 card a 相談 deletes under shop.delete."""
    if zemi_on(full) and d.get("produce") == "produce-007":
        return res
    target = full["cards"]["change_target"]
    if d.get("kind") == "card_change" and int(d.get("school_changes") or 0) >= 1 and \
            full["cards"].get("change_target_2", "same") != "same":
        target = full["cards"]["change_target_2"]
    if d.get("kind") == "card_change" and target in ("gained", "named"):
        pool = set(d.get("deck") or ()) | set(d.get("removable") or ())
        if target == "gained":
            hit = [k for k in _gained(d) if k in pool and not protection(full, k, "change", d.get("deck"))]
        else:
            hit = [k for want in full["cards"].get("change_cards") or ()
                   for k in sorted(pool) if k == want or ("+" not in want and k.split("+")[0] == want)]
        if hit:
            res["give_up"] = hit[0]
    if d.get("kind") == "card_change" and target == "first_school":
        pool = set(d.get("deck") or ()) | set(d.get("removable") or ())
        first = [k for k in d.get("school_gained") or () if k in pool and not protection(full, k, "change", d.get("deck"))]
        if first:
            res["give_up"] = first[0]
    if d.get("kind") == "card_change" and target == "advice" and res.get("mode") == "auto":
        pair = _advised_give_up(full, d, res.get("option"))
        if pair is not None:
            res["give_up"] = pair
            res["why"] = (res.get("why") or "") + "；变掉的按推荐"
    if d.get("kind") == "card_change" and target != "plan" and res.get("give_up") is None \
            and (d.get("removable") or d.get("deck")):
        res["give_up"] = give_up(full, d)
    if d.get("kind") == "shop" and full["shop"]["delete"] in GAINED_DELETES and res.get("mode") == "auto":
        hit = _gained_delete(d, full["shop"]["delete"])
        if hit is not None and res.get("option") == hit[0]:
            res["delete"] = hit[1]
            res["refresh_for_delete"] = bool(full["shop"]["refresh_for_delete"])
    return res


_PAIR = re.compile(r"^change (p_card-[^+ ]+)\+(\d) -> ")


def _advised_give_up(full, d, option):
    """the deck card the search's best pair for OPTION gives up (its label ``change <card>+<up> ->...``: catch_v keeps
    each offered card's best-rated pair), or None (no rated pair, or a card the plan protects)."""
    o = next((x for x in d.get("options") or () if x.get("id") == option), None)
    m = _PAIR.match(str((o or {}).get("label") or "")) if o and o.get("rating") is not None else None
    if not m:
        return None
    key = f"{m.group(1)}+{m.group(2)}"
    return None if protection(full, key, "change", d.get("deck")) else key


def _card_reroll(full, d, cards, lacking):
    """a card pick's 再抽选 by reroll.mode, or None (a card is taken)."""
    rid = d.get("reroll_id")
    if rid is None or int(d.get("rerolls_left") or 0) <= 0:
        return None
    rmode = (full.get("reroll") or {}).get("mode")
    left = f"（本局还能重抽 {int(d['rerolls_left'])} 次）"
    if rmode == "if_no_target" and (cards.get("must") or lacking):
        return {"mode": "auto", "option": rid, "why": "报价里没有目标卡，重抽" + left}
    if rmode == "by_v" and (d.get("v_reroll") or {}).get("worth"):
        return {"mode": "auto", "option": rid, "why": "按选卡包 V：新报价更可能有好卡，重抽" + left}
    return None


def _target_lacking(target, deck):
    """{card id: copies still to take} of cards.target against the deck now (any upgrade counts); {} without both."""
    if not target or deck is None:
        return {}
    have = {}
    for k in deck:
        cid = str(k).split("+")[0]
        have[cid] = have.get(cid, 0) + 1
    want = {}
    for key, n in target:
        cid = key.split("+")[0]
        want[cid] = want.get(cid, 0) + int(n)
    return {cid: n - have.get(cid, 0) for cid, n in want.items() if n > have.get(cid, 0)}


def route_match(token, name):
    """Whether a route.steps / route.avoid entry (a candidate name or a ROUTE_CHOICES token) names candidate NAME."""
    name = str(name or "")
    if token == name:
        return True
    if token in ATTRS:
        return bool(re.search(rf"(^|_)lesson_{token}(_|$)", name))
    if token.startswith("school_"):
        return name in (f"event_{token}", "event_school")
    return name in {"activity": ("event_activity",), "present": ("present", "fan_present"), "shop": ("shop",),
                    "rest": ("refresh",), "customize": ("customize",),
                    "business": ("business", "event_business")}.get(token, ())


def route_pick(token, ids):
    """The candidate a route.steps entry takes among IDS (the step's candidate names), or None: its own name, else
    for a lesson token SP and ★ before the plain one (never a hard lesson first), for a school token its own
    attribute before the common 授業."""
    hits = [i for i in ids if route_match(token, i)]
    if not hits:
        return None
    if token in hits:
        return token

    def rank(i):
        i = str(i)
        return ("_sp" in i) * 4 + ("_star" in i) * 2 - ("_hard" in i) * 8 + (i != "event_school")
    return max(hits, key=rank)


def editor_meta():
    """What the website's plan editor shows: the presets (whole), every choice with its label, the H.I.F step layout, the
    memory grades with their boxes.
    """
    try:
        from GakumasCLI.scenario import memory_cost as MC
        boxes = {MC.GRADES[n][0]: [MC.GRADES[n][1], MC.GRADES[n][2]] for n in sorted(MC.GRADES)}
    except Exception:                                           # noqa: BLE001
        boxes = {}
    try:
        from GakumasCLI.scenario import timeline as TL
        timelines = [[p["id"], p["name"]] for p in TL.PRESETS]
    except Exception:                                           # noqa: BLE001
        timelines = []
    return {"format": FORMAT, "share_prefix": SHARE_PREFIX, "default": DEFAULT_PRESET,
            "presets": [dict(p) for p in PRESETS], "kinds": KINDS, "modes": MODES, "route_modes": ROUTE_MODES,
            "reroll_modes": REROLL_MODES, "maa_modes": MAA_MODES, "goal_kinds": GOAL_KINDS,
            "grades": list(MEMORY_GRADES), "boxes": boxes, "route_choices": ROUTE_CHOICES,
            "layout": {k: [list(x) for x in v] for k, v in HIF_LAYOUT.items()}, "school_tags": SCHOOL_TAGS,
            "shop_buy": SHOP_BUY, "shop_drinks": SHOP_DRINKS, "shop_refresh": SHOP_REFRESH,
            "idle_tap": {"true": IDLE_TAP[True], "false": IDLE_TAP[False]},
            "stages": {"*": "所有审查", "hif.sel1": "选拔 1", "hif.sel2": "选拔 2", "hif.sel3": "选拔 3",
                       "hif.main1": "本战上半场", "hif.main2": "本战下半场"},
            "timelines": timelines,
            "score_tiers": [list(t) for t in SCORE_TIERS], "score_roles": SCORE_ROLES,
            "score_defaults": dict(SCORE_DEFAULTS), "tie_margin": TIE_MARGIN,
            "plan_modes": PLAN_MODES, "first_kinds": list(FIRST_KINDS), "criteria": CRITERIA,
            "shop_delete": SHOP_DELETE, "change_targets": CHANGE_TARGETS, "exam_budget": EXAM_BUDGET,
            "change_targets_2": CHANGE_TARGETS_2, "memory_card": MEMORY_CARD,
            "attr_prefs": ATTR_PREFS,
            "exam_drinks": EXAM_DRINKS, "retry_stages": list(RETRY_STAGES),
            "zemi": {"steps": PLAN_STEPS["008"], "layout": [list(x) for x in HIF_LAYOUT.get("produce-008", ())]}}


def _memory_pick(opts, deck, goal, held=()):
    """(option id, why) for a memory goal: the option after which the deck draws the wanted memory most often, or None
    when no option changes that chance.
    """
    try:
        from GakumasCLI.scenario import memory_cost as MC
        now = MC.p_target_of(deck, goal["grade"], goal["cards"], held)
        after = {o["id"]: MC.p_target_of(list(deck) + [_card_of(o)], goal["grade"], goal["cards"], held)
                 for o in opts if _card_of(o)}
    except (OSError, ValueError):
        return None
    if not after or all(abs(p - now) < 1e-9 for p in after.values()):
        return None
    best = max(after, key=after.get)
    return best, f"按目标记忆 {goal['grade']}：抽中率 {now:.0%} → {after[best]:.0%}（按现在的牌组）"


def _rank_cards(opts, adv, v, cards):
    """(option id, sure, why) for a card decision, or None when nothing ranks the options yet."""
    if adv.get("source") != "v" and adv.get("separated") and adv.get("lead") in [o["id"] for o in opts]:
        return adv["lead"], True, "搜索已分出胜负"
    prefer = cards.get("prefer") or {}
    avoid = cards.get("avoid") or ()

    def bias(o):
        k = _card_of(o)
        b = prefer.get(k, prefer.get(k.split("+")[0] if k else None, 0)) if k else 0
        return b - (5 if _matches(k, avoid) else 0)

    known = [o for o in opts if o["id"] in v]
    if adv.get("source") == "v" and known:
        best = max(known, key=lambda o: (bias(o), v[o["id"]]))
        why = "按选卡包 V" + ("" if len(known) == len(opts) else "（有 V 不认识的卡）")
        if adv.get("tie") == "sim" and best["id"] == adv.get("lead"):
            why = "选卡包 V 前两名接近，按模拟"
        if bias(best):
            why = "按你的偏好，再按选卡包 V"
        return best["id"], len(known) == len(opts), why
    if len(known) >= 2:
        best = max(known, key=lambda o: (bias(o), v[o["id"]]))
        return best["id"], len(known) == len(opts), "按终局牌组 V" + ("" if len(known) == len(opts) else "（有 V 不认识的卡）")
    rated = [o for o in opts if o.get("rating") is not None]
    if len(rated) == len(opts):
        best = max(rated, key=lambda o: (bias(o), o["rating"]))
        return best["id"], False, "按搜索（还没分出胜负）"
    if any(bias(o) for o in opts):
        best = max(opts, key=bias)
        return best["id"], False, "按你的偏好"
    return None
