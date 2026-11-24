# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""user audition timelines (gkms.timeline.v1)."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re
import zlib

FORMAT = "gkms.timeline.v1"
PLAN_FORMAT = "gkms.round_plan.v1"
MAX_BYTES = 64 * 1024
SHARE_PREFIX = "GKTL1:"
MAX_RULES = 64
MAX_KEYS = 48
HARD_BASE = 10 ** 12
HARD_STEP = 10 ** 9
VIOLATION = 10 ** 14
PEN_MAX = 1 << 62
MAJOR_SCALE = 1 << 64
OBJECTIVE_WORST = -4 * PEN_MAX * MAJOR_SCALE
WALL_LIMIT = 1800.0

HIF_STAGES = ("hif.sel1", "hif.sel2", "hif.sel3", "hif.main1", "hif.main2")
STAGE_OF = {("produce-007", "AUDITION_MID1"): "hif.sel1", ("produce-007", "AUDITION_MID2"): "hif.sel2",
            ("produce-007", "AUDITION_FINAL"): "hif.sel3", ("produce-008", "AUDITION_MID1"): "hif.main1",
            ("produce-008", "AUDITION_FINAL"): "hif.main2"}
STAGE_LABELS = {"*": "所有审查", "hif.sel1": "选拔 1", "hif.sel2": "选拔 2", "hif.sel3": "选拔 3",
                "hif.main1": "本战上半场", "hif.main2": "本战下半场"}
_STAGE_RE = re.compile(r"^(\*|hif\.(sel[123]|main[12])|nia\.[a-z0-9_]{1,40}|hajime\.[a-z0-9_]{1,40})$")
ALGORITHMS = ("inherit", "default", "alns10")
PLANS = ("sense", "logic", "anomaly")
PLAN_OF_TYPE = {1: "sense", 2: "logic", 3: "anomaly"}
TURN_TYPES = {"vocal": 1, "dance": 2, "visual": 3}
TAGS = {"active": 1, "mental": 2, "trouble": 3}
FIELDS = ("score", "stamina", "block", "lesson_buff", "review", "aggressive", "full_power_point", "lost_ssr", "debuffs")
FIELD_LABELS = {"score": "分数", "stamina": "体力", "block": "元气", "lesson_buff": "集中", "review": "好印象",
                "aggressive": "干劲", "full_power_point": "全力值", "lost_ssr": "除外的SSR技能卡", "debuffs": "低下状态"}
# core_rules.h RT_F_LOST_SSR / RT_F_DEBUFFS (exam rules 28).
COUNT_FIELDS = ("lost_ssr", "debuffs")


def milestone_value(node, field):
    """A milestone field's value on ``node`` (an ExamSession)."""
    st = node.state
    if field == "lost_ssr":
        cards = getattr(node.master, "cards", {})
        n = 0
        for card in getattr(node.deck, "lost", None) or ():
            row = cards.get((card.get("produce_card_id"), int(card.get("current_upgrade_count") or 0))) or \
                cards.get((card.get("produce_card_id"), 0)) or {}
            rarity = row.get("rarity") or {}
            n += int((rarity.get("number") if isinstance(rarity, dict) else rarity) or 0) == 4
        return n
    if field == "debuffs":
        return node.debuff_count()
    if field in SHARE_FIELDS:
        return split_shares(st)[SHARE_FIELDS.index(field)]
    return int(getattr(st, field, 0) or 0)


SHARE_FIELDS = ("share_vocal", "share_dance", "share_visual")
SPLIT_KEYS = ("vocal", "dance", "visual")
SPLIT_LABELS = {"vocal": "Vo", "dance": "Da", "visual": "Vi"}
SPLIT_WEIGHT = 10 ** 9


def split_shares(state):
    """(vocal, dance, visual) permil of the score's three parts (0s before any score); core_rules.h rt_field."""
    parts = [int(getattr(state, "score_" + k, 0) or 0) for k in SPLIT_KEYS]
    total = sum(parts)
    return tuple(x * 1000 // total if total > 0 else 0 for x in parts)


def split_check(spec):
    """(split, errors): the split in canonical form ({key: [lo, hi]} for the three keys), None when it holds no
    constraint (every range 0-100, or ``spec`` empty) -- with the reasons it is refused as text."""
    if not spec:
        return None, []
    if not isinstance(spec, dict):
        return None, ["控分格式不对"]
    out, errors = {}, []
    for key in SPLIT_KEYS:
        label = SPLIT_LABELS[key]
        row = spec.get(key, [0, 100])
        try:
            lo, hi = (int(round(float(x))) for x in row)
        except (TypeError, ValueError):
            errors.append(f"{label} 要填两个数")
            continue
        if not (0 <= lo <= 100 and 0 <= hi <= 100):
            errors.append(f"{label} 要在 0–100 之间")
        elif lo > hi:
            errors.append(f"{label} 的下限大于上限")
        out[key] = [lo, hi]
    if errors:
        return None, errors
    if sum(v[0] for v in out.values()) > 100:
        errors.append("三项下限加起来超过 100%，不可能同时满足")
    if sum(v[1] for v in out.values()) < 100:
        errors.append("三项上限加起来不到 100%，不可能同时满足")
    if errors:
        return None, errors
    if all(v == [0, 100] for v in out.values()):
        return None, []
    return out, []


def split_text(split):
    """「Vo 90–95% · Da ≤5% · Vi ≤5%」 (unconstrained parts left out)."""
    parts = []
    for key in SPLIT_KEYS:
        lo, hi = (split or {}).get(key, [0, 100])
        if (lo, hi) == (0, 100):
            continue
        label = SPLIT_LABELS[key]
        parts.append(f"{label} ≥{lo}%" if hi == 100 else f"{label} ≤{hi}%" if lo == 0
                     else f"{label} {lo}%" if lo == hi else f"{label} {lo}–{hi}%")
    return " · ".join(parts)


def split_rules(split):
    """The ms_end rules of a split (``split_check``'s canonical form); [] for None."""
    rules = []
    for key in SPLIT_KEYS:
        lo, hi = (split or {}).get(key, [0, 100])
        for op, value, needed in ((">=", lo, lo > 0), ("<=", hi, hi < 100)):
            if needed:
                rules.append({"mode": "ms_end", "target": "none", "keys": [], "abs": 0, "left": 0, "n": 0,
                              "field": "share_" + key, "op": op, "value": int(value) * 10,
                              "weight": float(SPLIT_WEIGHT), "penalty": 0, "split": True})
    return rules


def split_report(state, split):
    """What a line's final state gives against ``split``: {shares (permil), ok, distance (permil outside the
    ranges, what SPLIT_WEIGHT is paid on), text}."""
    shares = split_shares(state)
    distance = 0
    for key, x in zip(SPLIT_KEYS, shares):
        lo, hi = (split or {}).get(key, [0, 100])
        distance += max(0, lo * 10 - x) + max(0, x - hi * 10)
    return {"shares": list(shares), "ok": distance == 0, "distance": distance,
            "text": " · ".join(f"{SPLIT_LABELS[k]} {x / 10:.0f}%" for k, x in zip(SPLIT_KEYS, shares)),
            "score": int(getattr(state, "score", 0) or 0)}


GOAL_MODES = ("stamina", "score", "first", "custom")
GOAL_LABELS = {"stamina": "达标·最省体力", "score": "达标·最高分", "first": "第1名·最省体力", "custom": "自定义分数·最省体力"}
GOAL_MARGINS = (0, 50, 100, 200)
from GakumasCLI.produce.produce_effects import SELECTION_STAR_LINE as _SEL_LINE  # noqa: E402
STAR_LINE = {("produce-007", "AUDITION_MID1"): _SEL_LINE["mid1"], ("produce-007", "AUDITION_MID2"): _SEL_LINE["mid2"],
             ("produce-007", "AUDITION_FINAL"): _SEL_LINE["final"]}
GOAL_KIND_LABEL = {"sel1": "选拔赛 1", "sel2": "选拔赛 2", "sel3": "选拔赛 3", "hajime_mid": "初·中期考试",
                   "honsen1": "本战上半场", "honsen2": "本战下半场"}
HONSEN_KINDS = ("honsen1", "honsen2")
HONSEN_MODES = ("stamina", "custom")
GOAL_TARGET_WEIGHT = 1
GOAL_STAMINA_WEIGHT = 10 ** 5
GOAL_CLIP_WEIGHT = 0.999


def goal_kind(meta):
    """Which audition takes a goal: 'sel1' / 'sel2' / 'sel3' (the H.I.F 選抜), 'hajime_mid' (初's mid audition), or
    None (本戦, finals, N.I.A, lessons, the idol road)."""
    m = meta or {}
    tail = str(m.get("step_type") or "")
    if m.get("produce_id") == "produce-007":
        return {"AUDITION_MID1": "sel1", "AUDITION_MID2": "sel2", "AUDITION_FINAL": "sel3"}.get(tail)
    if m.get("produce_id") == "produce-008":
        return {"AUDITION_MID1": "honsen1", "AUDITION_FINAL": "honsen2"}.get(tail)
    if m.get("scenario") == "hajime" and tail == "AUDITION_MID1" and not m.get("lesson"):
        return "hajime_mid"
    return None


def honsen_cap(kind):
    """the 本戦 round's score where the H.I.F rating stops growing (produce_effects.hif_rating: the last finite regime of
    HIF_ROUND1 for the 上半场 -- its point / 1.2 is the score -- and of HIF_ROUND2 for the 下半场)."""
    from GakumasCLI.produce.produce_effects import HIF_ROUND1, HIF_ROUND2
    table = HIF_ROUND1 if kind == "honsen1" else HIF_ROUND2
    return int(max(th for th, _base, _mul in table if th != float("inf")))


def goal_modes(kind):
    """the goal modes an audition kind offers (the 本戦: 到封顶 / 自定义; 最高分 is its default, no goal)."""
    return HONSEN_MODES if kind in HONSEN_KINDS else GOAL_MODES


def goal_label(kind, mode):
    """a mode's label for an audition kind (the 本戦's 「到 140 万·最省体力」)."""
    if kind in HONSEN_KINDS and mode == "stamina":
        return f"到 {honsen_cap(kind) / 10000:g} 万·最省体力"
    return GOAL_LABELS.get(mode, mode)


def goal_default(meta):
    """The goal an audition starts with: 選抜1 / 選抜2 and a capped 初 mid save stamina at their line, 選抜3 keeps the score
    (its stamina does not reach the 本戦), an uncapped 初 mid saves stamina at first place; a 本戦 round has none (最高分, as
    before).
    """
    kind = goal_kind(meta)
    if kind is None or kind in HONSEN_KINDS:
        return None
    if kind == "sel3":
        return {"mode": "score", "margin": 0}
    if kind == "hajime_mid" and not (meta or {}).get("score_cap"):
        return {"mode": "first", "margin": 0}
    return {"mode": "stamina", "margin": 0}


def goal_check(spec):
    """(spec, errors): {"mode", "margin" (permil), "custom" (int, custom mode)} in canonical form; None for ``spec``
    None / "default" (the audition's own default)."""
    if spec in (None, "", "default"):
        return None, []
    if isinstance(spec, str):
        spec = {"mode": spec}
    if not isinstance(spec, dict):
        return None, ["目标格式不对"]
    mode = spec.get("mode")
    if mode not in GOAL_MODES:
        return None, [f"不认识的目标：{mode}"]
    try:
        margin = int(spec.get("margin") or 0)
    except (TypeError, ValueError):
        return None, ["余量要填数字"]
    if not 0 <= margin <= 1000:
        return None, ["余量要在 0–100% 之间"]
    out = {"mode": mode, "margin": margin}
    if mode == "custom":
        try:
            out["custom"] = int(round(float(spec.get("custom"))))
        except (TypeError, ValueError):
            return None, ["自定义分数要填一个数"]
        if out["custom"] <= 0:
            return None, ["自定义分数要大于 0"]
    return out, []


def goal_resolve(spec, meta, npc_scores=None, max_stamina=None):
    """(goal, why): the goal of an audition with ``meta`` -- spec None takes ``goal_default`` -- with its line:
    {mode, margin, custom?, kind, base (the line), source (what the line is), target (base with the margin, at least
    the pass line), pass_line (the rank_threshold-th NPC + 1, None unknown), max_stamina, label}.
    """
    kind = goal_kind(meta)
    if kind is None:
        return None, "这场审查没有目标选项"
    if kind in HONSEN_KINDS:
        if not spec:
            return None, None
        if spec.get("mode") not in HONSEN_MODES:
            return None, f"本战只有 最高分 / {' / '.join(goal_label(kind, m) for m in HONSEN_MODES)}"
    spec = dict(spec or goal_default(meta))
    npcs = sorted((int(x) for x in npc_scores or () if x is not None), reverse=True)
    rank = int((meta or {}).get("rank_threshold") or 0)
    pass_line = npcs[rank - 1] + 1 if rank and len(npcs) >= rank else None
    mode = spec["mode"]
    if mode == "first":
        if not npcs:
            return None, "不知道 NPC 的分数，没法定第 1 名的线"
        base, source = npcs[0] + 1, "第1名"
    elif mode == "custom":
        base, source = int(spec["custom"]), "自定义"
    elif kind in HONSEN_KINDS:
        base, source = honsen_cap(kind), "评价封顶"
    elif kind == "hajime_mid":
        cap = int((meta or {}).get("score_cap") or 0)
        if cap > 0:
            base, source = cap, "封顶分"
        elif npcs:
            base, source = npcs[0] + 1, "第1名"
        else:
            return None, "这场初中期考试没有封顶分，也不知道 NPC 的分数"
    else:
        base, source = STAR_LINE[((meta or {}).get("produce_id"), (meta or {}).get("step_type"))], "星上限"
    margin = int(spec.get("margin") or 0)
    target = -(-base * (1000 + margin) // 1000)
    if pass_line is not None:
        target = max(target, pass_line)
    goal = {"mode": mode, "margin": margin, "kind": kind, "base": int(base), "source": source,
            "target": int(target), "pass_line": pass_line, "max_stamina": int(max_stamina or 0) or None,
            "label": goal_label(kind, mode)}
    if mode == "custom":
        goal["custom"] = int(spec["custom"])
    return goal, None


def goal_text(goal):
    """「达标·最省体力 · 星上限 15,001」 (+「余量 5%」)."""
    if not goal:
        return None
    text = f"{goal['label']} · {goal['source']} {goal['target']:,}"
    if goal.get("margin"):
        text += f"（含余量 {goal['margin'] / 10:g}%）"
    return text


def goal_rules(goal):
    """The ms_end rules of a resolved goal (``goal_resolve``); [] for None."""
    if not goal:
        return []
    base = {"mode": "ms_end", "target": "none", "keys": [], "abs": 0, "left": 0, "n": 0, "penalty": 0, "goal": True}
    rules = [dict(base, mode="ms_goal", field="score", op=">=", value=int(goal["target"]),
                  weight=float(GOAL_TARGET_WEIGHT))]
    if goal["mode"] != "score":
        rules.append(dict(base, field="stamina", op=">=", value=int(goal.get("max_stamina") or 100),
                          weight=float(GOAL_STAMINA_WEIGHT)))
        rules.append(dict(base, field="score", op="<=", value=int(goal["target"]), weight=GOAL_CLIP_WEIGHT))
    return rules


def goal_report(state, goal):
    """What a line's final state gives against ``goal``: {score, target, reached, short, stamina, max_stamina}."""
    score = int(getattr(state, "score", 0) or 0)
    return {"score": score, "target": int(goal["target"]), "reached": score >= int(goal["target"]),
            "short": max(0, int(goal["target"]) - score), "stamina": int(getattr(state, "stamina", 0) or 0),
            "max_stamina": goal.get("max_stamina")}
ROOT_MODES = ("off", "wave1", "diverse", "scored")

# budget field: (kind, low, high)
BUDGET = {"open_seconds": (float, 0, 1800), "open_runs": (int, 1, 5), "restarts": (int, 1, 16),
          "stabilization": (int, 1, 8), "quick_seconds": (float, 1, 1800), "resolve_seconds": (float, 1, 1800),
          "open_tail_seconds": (float, 0, 600), "resolve_tail_seconds": (float, 0, 600),
          "alns_rounds": (int, 0, 50), "alns_job_seconds": (float, 1, 600), "er_job_seconds": (float, 1, 600),
          "er_elites": (int, 1, 16), "max_cores": (int, 1, 256), "stop_score": (int, 0, 10 ** 9)}
EXHAUST = {"tail_turns": (int, 1, 6), "tail_seconds": (float, 0, 600)}
# mode codes shared with core_rules.h
MODE = {"forbid": 0, "require": 1, "first": 2, "prefer": 3, "cap": 4, "ms_hard": 5, "ms_soft": 6, "no_skip": 7,
        "ms_end": 8,
        "ms_goal": 9}
TARGET = {"drink": 0, "end": 1, "play": 2, "none": 3}
HARD_MODES = ("require", "first", "ms_hard")
ALL_TURNS = (1 << 64) - 2


class TimelineError(ValueError):
    """A timeline the validator refuses; ``errors`` lists every problem as text."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors[:5]) + (" ..." if len(self.errors) > 5 else ""))


# ------------------------------------------------------------------ helpers
def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(obj):
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def timeline_id(doc):
    """The document's own id, or one derived from its content."""
    tid = doc.get("id") if isinstance(doc, dict) else None
    if isinstance(tid, str) and re.match(r"^[A-Za-z0-9_.-]{1,64}$", tid):
        return tid
    return "tl_" + digest({k: v for k, v in (doc or {}).items() if k != "id"})[:12]


def share_code(doc):
    raw = canonical(doc).encode("utf-8")
    return SHARE_PREFIX + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")


def from_share_code(text):
    text = str(text or "").strip()
    if not text.startswith(SHARE_PREFIX):
        raise TimelineError(["分享码要以 GKTL1: 开头"])
    body = text[len(SHARE_PREFIX):]
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)), bufsize=MAX_BYTES)
        if len(raw) > MAX_BYTES:
            raise ValueError("too large")
        doc = json.loads(raw.decode("utf-8"))
    except Exception as exc:                                    # noqa: BLE001
        raise TimelineError([f"分享码无法解开: {type(exc).__name__}"]) from exc
    validate(doc)
    return doc


def stage_key(meta):
    """The stage key of an audition from its loadout meta (hif_loadout_session meta / the round record)."""
    meta = meta or {}
    produce_id = str(meta.get("produce_id") or "")
    step = str(meta.get("step_type") or meta.get("step_tail") or "").upper()
    key = STAGE_OF.get((produce_id, step))
    if key:
        return key
    scenario = str(meta.get("scenario") or "")
    if not scenario:
        try:
            from GakumasCLI.scenario import nia, hajime
            scenario = "nia" if nia.is_nia(produce_id) else ("hajime" if hajime.is_hajime(produce_id) else "")
        except Exception:                                       # noqa: BLE001
            scenario = ""
    if scenario in ("nia", "hajime") and step:
        return f"{scenario}.{re.sub(r'[^a-z0-9_]', '_', step.lower())[:40]}"
    return None


# ------------------------------------------------------------------ validation
def _num(errors, where, value, kind, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind is int and int(value) != value):
        errors.append(f"{where}: 要{'整数' if kind is int else '数字'}")
        return None
    if not math.isfinite(float(value)) or not lo <= value <= hi:
        errors.append(f"{where}: 范围 {lo}–{hi}")
        return None
    return kind(value)


def _turns_ok(errors, where, spec, depth=0):
    if spec is None or spec == "all":
        return
    if isinstance(spec, list):
        if len(spec) != 2 or not all(isinstance(x, int) and not isinstance(x, bool) for x in spec) \
                or not 1 <= spec[0] <= spec[1] <= 60:
            errors.append(f"{where}.turns: 写成 [起, 止]，1 ≤ 起 ≤ 止 ≤ 60")
        return
    if not isinstance(spec, dict) or depth > 2:
        errors.append(f"{where}.turns: 不认识的回合写法")
        return
    keys = set(spec)
    if keys == {"turns"}:
        _turns_ok(errors, where, spec["turns"], depth + 1)
    elif keys == {"from_end"}:
        v = spec["from_end"]
        if not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in v)
                and 1 <= v[0] <= v[1] <= 60):
            errors.append(f"{where}.turns.from_end: 写成 [a, b]，1 ≤ a ≤ b ≤ 60（1 = 最后一回合）")
    elif keys == {"type"}:
        if spec["type"] not in TURN_TYPES:
            errors.append(f"{where}.turns.type: vocal / dance / visual")
    elif keys == {"any"}:
        if not isinstance(spec["any"], list) or not 1 <= len(spec["any"]) <= 8:
            errors.append(f"{where}.turns.any: 1–8 个回合写法")
        else:
            for k, s in enumerate(spec["any"]):
                _turns_ok(errors, f"{where}.any[{k}]", s, depth + 1)
    else:
        errors.append(f"{where}.turns: 不认识的回合写法 {sorted(keys)}")


def _id_ok(value):
    return isinstance(value, str) and 1 <= len(value) <= 80 and re.match(r"^[A-Za-z0-9_\-.]+$", value)


def _only(errors, where, item, allowed):
    extra = sorted(set(item) - set(allowed))
    if extra:
        errors.append(f"{where}: 不认识的字段 {extra}")


def _check_stage(errors, where, st, stage_name):
    if not isinstance(st, dict):
        errors.append(f"{where}: 要一个对象")
        return
    _only(errors, where, st, ("drinks", "plays", "turns", "exhaust", "budget", "resolve", "milestones",
                              "drink_table", "note"))
    for k, d in enumerate(st.get("drinks") or ()):
        w = f"{where}.drinks[{k}]"
        if not isinstance(d, dict):
            errors.append(f"{w}: 要一个对象")
            continue
        _only(errors, w, d, ("drink", "rule", "turns", "weight", "stage", "note"))
        if d.get("drink") != "*" and not _id_ok(d.get("drink")):
            errors.append(f"{w}.drink: 饮料 id 或 \"*\"")
        rule = d.get("rule")
        if rule not in ("forbid", "require", "first", "prefer", "keep_to"):
            errors.append(f"{w}.rule: forbid / require / first / prefer / keep_to")
        if rule == "prefer":
            wv = _num(errors, f"{w}.weight", d.get("weight", 1), float, -5, 5)
            if wv == 0:
                errors.append(f"{w}.weight: 不能为 0")
        if rule == "keep_to":
            target = d.get("stage")
            if target not in HIF_STAGES:
                errors.append(f"{w}.stage: 留到哪一场（{' / '.join(HIF_STAGES)}）")
            elif stage_name in HIF_STAGES and HIF_STAGES.index(target) <= HIF_STAGES.index(stage_name):
                errors.append(f"{w}.stage: 要在本场之后")
            if d.get("drink") == "*":
                errors.append(f"{w}.drink: keep_to 要指定一瓶")
        _turns_ok(errors, w, d.get("turns"))
    for k, p in enumerate(st.get("plays") or ()):
        w = f"{where}.plays[{k}]"
        if not isinstance(p, dict):
            errors.append(f"{w}: 要一个对象")
            continue
        _only(errors, w, p, ("card", "upgrade", "tag", "rule", "turns", "weight", "note"))
        if ("card" in p) == ("tag" in p):
            errors.append(f"{w}: card 和 tag 二选一")
        elif "card" in p and not _id_ok(p["card"]):
            errors.append(f"{w}.card: 卡 id")
        elif "tag" in p and p["tag"] not in TAGS:
            errors.append(f"{w}.tag: {' / '.join(TAGS)}")
        if p.get("upgrade") is not None:
            _num(errors, f"{w}.upgrade", p["upgrade"], int, 0, 9)
        if p.get("rule") not in ("forbid", "require", "first", "prefer"):
            errors.append(f"{w}.rule: forbid / require / first / prefer")
        if p.get("rule") == "prefer":
            wv = _num(errors, f"{w}.weight", p.get("weight", 1), float, -5, 5)
            if wv == 0:
                errors.append(f"{w}.weight: 不能为 0")
        _turns_ok(errors, w, p.get("turns"))
    for k, t in enumerate(st.get("turns") or ()):
        w = f"{where}.turns[{k}]"
        if not isinstance(t, dict):
            errors.append(f"{w}: 要一个对象")
            continue
        _only(errors, w, t, ("rule", "n", "turns", "note"))
        if t.get("rule") not in ("cap", "no_skip"):
            errors.append(f"{w}.rule: cap / no_skip")
        if t.get("rule") == "cap":
            _num(errors, f"{w}.n", t.get("n"), int, 0, 9)
        _turns_ok(errors, w, t.get("turns"))
    ex = st.get("exhaust")
    if ex is not None:
        w = f"{where}.exhaust"
        if not isinstance(ex, dict):
            errors.append(f"{w}: 要一个对象")
        else:
            _only(errors, w, ex, tuple(EXHAUST) + ("root", "branch_at", "sweep", "cut_table"))
            for name, (kind, lo, hi) in EXHAUST.items():
                if name in ex:
                    _num(errors, f"{w}.{name}", ex[name], kind, lo, hi)
            if "root" in ex and ex["root"] not in ROOT_MODES:
                errors.append(f"{w}.root: {' / '.join(ROOT_MODES)}")
            for k, b in enumerate(ex.get("branch_at") or ()):
                bw = f"{w}.branch_at[{k}]"
                if not isinstance(b, dict):
                    errors.append(f"{bw}: 要一个对象")
                    continue
                _only(errors, bw, b, ("turns", "cap", "seconds", "keep"))
                _turns_ok(errors, bw, b.get("turns"))
                _num(errors, f"{bw}.cap", b.get("cap", 200), int, 1, 2000)
                _num(errors, f"{bw}.seconds", b.get("seconds", 1.0), float, 0.2, 30)
                _num(errors, f"{bw}.keep", b.get("keep", 4), int, 1, 16)
            if len(ex.get("branch_at") or ()) > 4:
                errors.append(f"{w}.branch_at: 最多 4 个")
            for k, s in enumerate(ex.get("sweep") or ()):
                sw = f"{w}.sweep[{k}]"
                if not isinstance(s, dict):
                    errors.append(f"{sw}: 要一个对象")
                    continue
                _only(errors, sw, s, ("drink", "card", "upgrade", "turns", "seconds"))
                if ("drink" in s) == ("card" in s):
                    errors.append(f"{sw}: drink 和 card 二选一")
                elif not _id_ok(s.get("drink") or s.get("card")):
                    errors.append(f"{sw}: 饮料 / 卡 id")
                _turns_ok(errors, sw, s.get("turns"))
                _num(errors, f"{sw}.seconds", s.get("seconds", 15), float, 1, 120)
            if len(ex.get("sweep") or ()) > 4:
                errors.append(f"{w}.sweep: 最多 4 个")
            ct = ex.get("cut_table")
            if ct is not None:
                if not isinstance(ct, dict) or not ct:
                    errors.append(f"{w}.cut_table: {{剩余回合: 权重}}")
                else:
                    for left, wt in ct.items():
                        if not (str(left).isdigit() and 1 <= int(left) <= 40):
                            errors.append(f"{w}.cut_table: 键是剩余回合 1–40")
                        _num(errors, f"{w}.cut_table[{left}]", wt, float, 0, 1000)
    bd = st.get("budget")
    if bd is not None:
        w = f"{where}.budget"
        if not isinstance(bd, dict):
            errors.append(f"{w}: 要一个对象")
        else:
            _only(errors, w, bd, tuple(BUDGET))
            for name, (kind, lo, hi) in BUDGET.items():
                if name in bd:
                    _num(errors, f"{w}.{name}", bd[name], kind, lo, hi)
    for k, r in enumerate(st.get("resolve") or ()):
        w = f"{where}.resolve[{k}]"
        if not isinstance(r, dict):
            errors.append(f"{w}: 要一个对象")
            continue
        _only(errors, w, r, ("turns", "seconds"))
        _turns_ok(errors, w, r.get("turns"))
        _num(errors, f"{w}.seconds", r.get("seconds", 60), float, 1, 1800)
    for k, m in enumerate(st.get("milestones") or ()):
        w = f"{where}.milestones[{k}]"
        if not isinstance(m, dict):
            errors.append(f"{w}: 要一个对象")
            continue
        _only(errors, w, m, ("at", "turns", "field", "op", "value", "mode", "weight", "note"))
        if m.get("at", "turn_end") != "turn_end":
            errors.append(f"{w}.at: 只有 turn_end")
        if m.get("field") not in FIELDS:
            errors.append(f"{w}.field: {' / '.join(FIELDS)}")
        if m.get("op") not in (">=", "<="):
            errors.append(f"{w}.op: >= / <=")
        _num(errors, f"{w}.value", m.get("value"), int, -10 ** 9, 10 ** 9)
        if m.get("mode", "soft") not in ("hard", "soft"):
            errors.append(f"{w}.mode: hard / soft")
        if m.get("mode", "soft") == "soft":
            _num(errors, f"{w}.weight", m.get("weight", 1), float, 0, 10 ** 6)
        _turns_ok(errors, w, m.get("turns"))
    if st.get("drink_table") not in (None, "skip", "run"):
        errors.append(f"{where}.drink_table: skip / run")
    if isinstance(bd, dict) and {"open_seconds", "open_runs", "alns_rounds", "open_tail_seconds"} & set(bd):
        try:
            wall = wall_estimate({"budget": bd}, default_open=360.0)
        except (TypeError, ValueError):
            wall = 0
        if wall > WALL_LIMIT:
            errors.append(f"{where}.budget: 开局约 {wall:.0f} s，超过单次解算上限 {WALL_LIMIT:.0f} s")
    n_rules = sum(len(st.get(t) or ()) for t in ("drinks", "plays", "turns", "milestones"))
    if n_rules > MAX_RULES:
        errors.append(f"{where}: 规则最多 {MAX_RULES} 条（现在 {n_rules}）")


def validate(doc):
    """Raise TimelineError listing every problem; return the document's id when it is fine."""
    errors = []
    if not isinstance(doc, dict):
        raise TimelineError(["时间轴要是一个 JSON 对象"])
    if len(canonical(doc).encode("utf-8")) > MAX_BYTES:
        raise TimelineError([f"时间轴超过 {MAX_BYTES // 1024} KB"])
    if doc.get("format") != FORMAT:
        errors.append(f"format 要是 {FORMAT}")
    _only(errors, "时间轴", doc, ("format", "id", "name", "note", "scope", "algorithm", "stages", "created",
                                "updated", "author", "source"))
    if not isinstance(doc.get("name"), str) or not 1 <= len(doc["name"]) <= 60:
        errors.append("name: 1–60 个字")
    if doc.get("algorithm", "inherit") not in ALGORITHMS:
        errors.append(f"algorithm: {' / '.join(ALGORITHMS)}")
    scope = doc.get("scope") or {}
    if not isinstance(scope, dict):
        errors.append("scope: 要一个对象")
    else:
        _only(errors, "scope", scope, ("scenarios", "plans", "idols"))
        for s in scope.get("scenarios") or ():
            if s not in ("hif", "nia", "hajime"):
                errors.append("scope.scenarios: hif / nia / hajime")
        for p in scope.get("plans") or ():
            if p not in PLANS:
                errors.append(f"scope.plans: {' / '.join(PLANS)}")
        for i in scope.get("idols") or ():
            if not (isinstance(i, str) and re.match(r"^[a-z]{4}$", i)):
                errors.append("scope.idols: 偶像的 4 字母代号（如 hski）")
    stages = doc.get("stages")
    if not isinstance(stages, dict) or not stages:
        errors.append("stages: 至少写一场（或 \"*\"）")
    else:
        for name, st in stages.items():
            if not _STAGE_RE.match(str(name)):
                errors.append(f"stages: 不认识的审查键 {name!r}")
                continue
            _check_stage(errors, f"stages[{name}]", st, name)
    if errors:
        raise TimelineError(errors)
    return timeline_id(doc)


def check(doc):
    """[] or the validator's errors (for the editor)."""
    try:
        validate(doc)
        return []
    except TimelineError as exc:
        return exc.errors


# ------------------------------------------------------------------ scope / merge
def applies(doc, meta):
    """(True, None) or (False, reason) -- whether the timeline is meant for this audition."""
    scope = (doc or {}).get("scope") or {}
    key = stage_key(meta)
    if key is None:
        return False, "这场审查没有审查键"
    scenario = key.split(".")[0]
    if scope.get("scenarios") and scenario not in scope["scenarios"]:
        return False, f"剧本 {scenario} 不在范围内"
    plan = PLAN_OF_TYPE.get(_plan_type(meta))
    if scope.get("plans") and plan not in scope["plans"]:
        return False, f"计划类型 {plan or '?'} 不在范围内"
    idol = str((meta or {}).get("idol_card_id") or "")[7:11]
    if scope.get("idols") and idol not in scope["idols"]:
        return False, f"偶像 {idol or '?'} 不在范围内"
    stages = doc.get("stages") or {}
    if key not in stages and "*" not in stages:
        return False, f"没有写 {STAGE_LABELS.get(key, key)}"
    return True, None


def _plan_type(meta):
    v = (meta or {}).get("plan_type")
    if isinstance(v, dict):
        v = v.get("number")
    if isinstance(v, str):
        v = {"ProducePlanType_Plan1": 1, "ProducePlanType_Plan2": 2, "ProducePlanType_Plan3": 3}.get(v, v)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def merged_stage(doc, key):
    """``*`` overlaid by the stage's own entry: dict tracks merge key by key (the stage wins), list tracks
    concatenate (``*`` first)."""
    stages = (doc or {}).get("stages") or {}
    base, own = stages.get("*") or {}, stages.get(key) or {}
    out = {}
    for name in ("drinks", "plays", "turns", "resolve", "milestones"):
        rows = [dict(r, _from="*", _index=i) for i, r in enumerate(base.get(name) or ())]
        rows += [dict(r, _from=key, _index=i) for i, r in enumerate(own.get(name) or ())]
        if rows:
            out[name] = rows
    for name in ("exhaust", "budget"):
        d = dict(base.get(name) or {})
        d.update(own.get(name) or {})
        if d:
            out[name] = d
    dt = own.get("drink_table", base.get("drink_table"))
    if dt is not None:
        out["drink_table"] = dt
    return out


# ------------------------------------------------------------------ compile
class StageContext:
    """What compile needs to know about one audition."""

    def __init__(self, key, limit_turn, turn_types=(), drinks=(), deck=(), categories=None, meta=None):
        self.key = key
        self.limit_turn = int(limit_turn)
        self.turn_types = list(turn_types or ())
        self.drinks = list(drinks or ())
        self.deck = [(str(c), int(u)) for c, u in deck or ()]
        self.categories = dict(categories or {})
        self.meta = dict(meta or {})


def context_from_session(session, meta, master=None):
    """StageContext of a live / loadout session."""
    deck = []
    for zone in ("deck", "hand", "grave", "hold", "lost"):
        for card in getattr(session.deck, zone, ()) or ():
            pair = (card["produce_card_id"], int(card.get("current_upgrade_count", 0) or 0))
            if pair not in deck:
                deck.append(pair)
    categories = {}
    cards = getattr(master or session.master, "cards", None) or {}
    for cid, upg in deck:
        row = cards.get((cid, upg)) if isinstance(cards, dict) else None
        if row is None and isinstance(cards, dict):
            row = next((v for k, v in cards.items() if isinstance(k, tuple) and k[0] == cid), None)
        cat = (row or {}).get("category") if isinstance(row, dict) else None
        if isinstance(cat, dict):
            cat = cat.get("number")
        if isinstance(cat, str):
            cat = {"ProduceCardCategory_ActiveSkill": 1, "ProduceCardCategory_MentalSkill": 2,
                   "ProduceCardCategory_Trouble": 3}.get(cat)
        if cat is not None:
            categories[cid] = int(cat)
    types = list(getattr(session.deck, "turn_parameter_types", None) or ())
    limit = int(getattr(session.config, "limit_turn", 0) or getattr(session.state, "limit_turn", 0) or 0)
    return StageContext(stage_key(meta), limit, types, list(session.drinks), deck, categories, meta)


def turn_masks(spec, ctx):
    """(abs_mask, left_mask, ok): bit t of abs_mask = turn t, bit l of left_mask = l turns left (1 = last)."""
    if spec is None or spec == "all":
        return ALL_TURNS, 0, True
    if isinstance(spec, list):
        a, b = int(spec[0]), int(spec[1])
        return sum(1 << t for t in range(a, min(b, 63) + 1)), 0, True
    if "turns" in spec:
        return turn_masks(spec["turns"], ctx)
    if "from_end" in spec:
        a, b = spec["from_end"]
        return 0, sum(1 << t for t in range(int(a), min(int(b), 63) + 1)), True
    if "type" in spec:
        want = TURN_TYPES[spec["type"]]
        if not ctx.turn_types:
            return 0, 0, False
        return sum(1 << (i + 1) for i, t in enumerate(ctx.turn_types[:63]) if int(t or 0) == want), 0, True
    am = lm = 0
    ok = False
    for s in spec.get("any") or ():
        a, l, o = turn_masks(s, ctx)
        am, lm, ok = am | a, lm | l, ok or o
    return am, lm, ok


def _in_round(am, lm, ctx):
    """Whether a mask touches this audition (turns 1..limit + 3 extra, or any turns-left bit)."""
    if lm:
        return True
    return bool(am & sum(1 << t for t in range(1, min(63, ctx.limit_turn + 3) + 1)))


def mask_turns(am, lm, limit):
    """The absolute turns a mask covers in a round of ``limit`` turns (no extra turns)."""
    return [t for t in range(1, limit + 1) if (am >> t) & 1 or (lm >> (limit - t + 1)) & 1]


def compile_stage(doc, ctx, *, strict=True):
    """The RoundPlan of ``doc`` for the audition ``ctx``."""
    validate(doc)
    key = ctx.key
    st = merged_stage(doc, key)
    plan = {"format": PLAN_FORMAT, "timeline": {"id": timeline_id(doc), "name": doc.get("name")}, "stage": key,
            "limit_turn": ctx.limit_turn, "algorithm": None, "budget": {}, "exhaust": {}, "resolve": [],
            "rules": [], "keep": [], "drink_table": st.get("drink_table") or "skip", "dropped": []}
    if doc.get("algorithm") in ("default", "alns10"):
        plan["algorithm"] = doc["algorithm"]
    plan["budget"] = dict(sorted((st.get("budget") or {}).items()))
    ex = st.get("exhaust") or {}
    exo = {k: ex[k] for k in ("tail_turns", "tail_seconds", "root") if k in ex}
    if ex.get("cut_table"):
        exo["cut_table"] = {str(int(k)): float(v) for k, v in sorted(ex["cut_table"].items(), key=lambda kv: int(kv[0]))}
    drinks_here = set(ctx.drinks)
    deck_ids = {c for c, _ in ctx.deck}

    def drop(track, row, reason):
        plan["dropped"].append({"track": track, "from": row.get("_from"), "index": row.get("_index"),
                                "reason": reason})

    branch = []
    for b in ex.get("branch_at") or ():
        am, lm, ok = turn_masks(b.get("turns"), ctx)
        turns = mask_turns(am, lm, ctx.limit_turn) if ok else []
        if not turns:
            drop("exhaust.branch_at", {"_from": key, "_index": len(branch)}, "回合不在本场")
            continue
        branch.append({"turns": turns, "cap": int(b.get("cap", 200)), "seconds": float(b.get("seconds", 1.0)),
                       "keep": int(b.get("keep", 4))})
    if branch:
        exo["branch_at"] = branch
    sweep = []
    for s in ex.get("sweep") or ():
        am, lm, ok = turn_masks(s.get("turns"), ctx)
        turns = mask_turns(am, lm, ctx.limit_turn) if ok else []
        if "drink" in s and s["drink"] not in drinks_here:
            drop("exhaust.sweep", {"_from": key, "_index": len(sweep)}, f"本场没有饮料 {s['drink']}")
            continue
        if "card" in s and s["card"] not in deck_ids:
            drop("exhaust.sweep", {"_from": key, "_index": len(sweep)}, f"本场牌组没有 {s['card']}")
            continue
        if not turns:
            drop("exhaust.sweep", {"_from": key, "_index": len(sweep)}, "回合不在本场")
            continue
        row = {"turns": turns, "seconds": float(s.get("seconds", 15))}
        if "drink" in s:
            row["drink"] = s["drink"]
        else:
            row["card"], row["upgrade"] = s["card"], s.get("upgrade")
        sweep.append(row)
    if sweep:
        exo["sweep"] = sweep
    plan["exhaust"] = exo
    for r in st.get("resolve") or ():
        am, lm, ok = turn_masks(r.get("turns"), ctx)
        if not ok or not _in_round(am, lm, ctx):
            drop("resolve", r, "回合不在本场")
            continue
        plan["resolve"].append({"abs": am, "left": lm, "seconds": float(r.get("seconds", 60))})
    rules = []
    for d in st.get("drinks") or ():
        if d["rule"] == "keep_to":
            if d["drink"] not in drinks_here:
                drop("drinks", d, f"本场没有饮料 {d['drink']}")
                continue
            if d["drink"] not in plan["keep"]:
                plan["keep"].append(d["drink"])
            rules.append({"track": "drinks", "from": d["_from"], "index": d["_index"], "mode": "forbid",
                          "target": "drink", "keys": [[d["drink"], -1]], "abs": ALL_TURNS, "left": 0,
                          "keep_to": d["stage"]})
            continue
        am, lm, ok = turn_masks(d.get("turns"), ctx)
        if not ok:
            drop("drinks", d, "本场不知道各回合的参数类型")
            continue
        if not _in_round(am, lm, ctx):
            drop("drinks", d, "回合不在本场")
            continue
        if d["drink"] != "*" and d["drink"] not in drinks_here:
            drop("drinks", d, f"本场没有饮料 {d['drink']}")
            continue
        rule = {"track": "drinks", "from": d["_from"], "index": d["_index"], "mode": d["rule"], "target": "drink",
                "keys": [] if d["drink"] == "*" else [[d["drink"], -1]], "abs": am, "left": lm}
        if d["rule"] == "prefer":
            rule["weight"] = float(d.get("weight", 1))
        rules.append(rule)
    for p in st.get("plays") or ():
        am, lm, ok = turn_masks(p.get("turns"), ctx)
        if not ok:
            drop("plays", p, "本场不知道各回合的参数类型")
            continue
        if not _in_round(am, lm, ctx):
            drop("plays", p, "回合不在本场")
            continue
        if "card" in p:
            if p["card"] not in deck_ids:
                drop("plays", p, f"本场牌组没有 {p['card']}")
                continue
            keys = [[p["card"], -1 if p.get("upgrade") is None else int(p["upgrade"])]]
        else:
            want = TAGS[p["tag"]]
            ids = [c for c in dict.fromkeys(c for c, _ in ctx.deck) if ctx.categories.get(c) == want]
            if not ids:
                drop("plays", p, f"本场牌组没有 {p['tag']} 卡")
                continue
            if len(ids) > MAX_KEYS:
                drop("plays", p, f"{p['tag']} 卡超过 {MAX_KEYS} 种")
                continue
            keys = [[c, -1] for c in ids]
        rule = {"track": "plays", "from": p["_from"], "index": p["_index"], "mode": p["rule"], "target": "play",
                "keys": keys, "abs": am, "left": lm}
        if p["rule"] == "prefer":
            rule["weight"] = float(p.get("weight", 1))
        rules.append(rule)
    for t in st.get("turns") or ():
        am, lm, ok = turn_masks(t.get("turns"), ctx)
        if not ok or not _in_round(am, lm, ctx):
            drop("turns", t, "回合不在本场")
            continue
        if t["rule"] == "cap":
            rules.append({"track": "turns", "from": t["_from"], "index": t["_index"], "mode": "cap",
                          "target": "play", "keys": [], "abs": am, "left": lm, "n": int(t["n"])})
        else:
            rules.append({"track": "turns", "from": t["_from"], "index": t["_index"], "mode": "no_skip",
                          "target": "end", "keys": [], "abs": am, "left": lm})
    for m in st.get("milestones") or ():
        am, lm, ok = turn_masks(m.get("turns"), ctx)
        if not ok or not _in_round(am, lm, ctx):
            drop("milestones", m, "回合不在本场")
            continue
        hard = m.get("mode", "soft") == "hard"
        rule = {"track": "milestones", "from": m["_from"], "index": m["_index"], "mode": "ms_hard" if hard else "ms_soft",
                "target": "none", "keys": [], "abs": am, "left": lm, "field": m["field"], "op": m["op"],
                "value": int(m["value"])}
        if not hard:
            rule["weight"] = float(m.get("weight", 1))
        rules.append(rule)
    if len(rules) > MAX_RULES:
        raise TimelineError([f"本场规则 {len(rules)} 条，最多 {MAX_RULES} 条"])
    conflicts = _conflicts(rules, ctx)
    if conflicts and strict:
        raise TimelineError(conflicts)
    hard = [r for r in rules if r["mode"] in HARD_MODES]
    for rank, r in enumerate(hard):
        r["penalty"] = HARD_BASE + (len(hard) - rank) * HARD_STEP
    for r in rules:
        r.setdefault("n", 0)
        r.setdefault("field", "")
        r.setdefault("op", ">=")
        r.setdefault("value", 0)
        r.setdefault("weight", 0.0)
        r.setdefault("penalty", 0)
    plan["rules"] = rules
    plan["hash"] = digest({k: v for k, v in plan.items() if k != "hash"})
    return plan


def _covered(req_keys, forb_keys):
    """Whether every action a require / first rule accepts is one the forbid rule removes."""
    if not forb_keys:
        return True
    if not req_keys:
        return False
    fb = {(k, int(u)) for k, u in forb_keys}
    return all((k, -1) in fb or (int(u) >= 0 and (k, int(u)) in fb) for k, u in req_keys)


def _conflicts(rules, ctx):
    """A require / first rule whose every action and turn some forbid rule removes."""
    out = []
    lim = ctx.limit_turn
    for req in rules:
        if req["mode"] not in ("require", "first") or req["target"] not in ("drink", "play"):
            continue
        ta = set(mask_turns(req["abs"], req["left"], lim))
        for forb in rules:
            if forb["mode"] != "forbid" or forb["target"] != req["target"]:
                continue
            if not _covered(req["keys"], forb["keys"]):
                continue
            tb = set(mask_turns(forb["abs"], forb["left"], lim))
            if ta and ta <= tb:
                out.append(f"{_where(req)} 和 {_where(forb)} 矛盾：要求的回合全被禁止")
    return out


def _where(rule):
    return f"{STAGE_LABELS.get(rule['from'], rule['from'])}.{rule['track']}[{rule['index']}]"


# ------------------------------------------------------------------ rules at run time
_M_FORBID, _M_REQUIRE, _M_FIRST, _M_PREFER, _M_CAP, _M_MSH, _M_MSS, _M_NOSKIP, _M_MSE, _M_MSG = range(10)


def _weighted(weight, short):
    """weight x shortfall truncated toward zero, within +-PEN_MAX (core_rules.h rt_weighted)."""
    d = float(weight) * float(short)
    if d >= float(PEN_MAX):
        return PEN_MAX
    if d <= -float(PEN_MAX):
        return -PEN_MAX
    return int(d)


def _sat_add(a, b):
    """core_rules.h rt_sat_add."""
    return max(-PEN_MAX, min(PEN_MAX, a + b))
_T_DRINK, _T_END, _T_PLAY, _T_NONE = range(4)


class RuleState:
    """Where a line stands against the rules: the turn of the last decision, actions and plays in it, done bits
    (require / first met), failed bits (hard milestones missed), accumulated soft penalty.
    """

    __slots__ = ("turn", "acts", "plays", "done", "failed", "pen")

    def __init__(self, turn=-1, acts=0, plays=0, done=0, failed=0, pen=0):
        self.turn, self.acts, self.plays, self.done, self.failed, self.pen = turn, acts, plays, done, failed, pen

    def copy(self):
        return RuleState(self.turn, self.acts, self.plays, self.done, self.failed, self.pen)

    def as_dict(self):
        return {"turn": self.turn, "acts": self.acts, "plays": self.plays, "done": self.done, "failed": self.failed,
                "pen": self.pen}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(int(d.get("turn", -1)), int(d.get("acts", 0)), int(d.get("plays", 0)), int(d.get("done", 0)),
                   int(d.get("failed", 0)), int(d.get("pen", 0)))


def _turn_left(node):
    turn = int(node.turn)
    return turn, int(node.config.limit_turn) + int(getattr(node.state, "extra_turns", 0) or 0) - turn + 1


def _active(r, turn, left):
    return bool((0 <= turn < 64 and (r[4] >> turn) & 1) or (1 <= left < 64 and (r[5] >> left) & 1))


def _later(r, turn, left):
    """Whether the rule is active in a later turn of this round: absolute turns turn+1 .."""
    if left <= 1:
        return False
    hi = min(63, turn + left - 1)
    if turn + 1 <= hi and r[4] & (((1 << (hi + 1)) - 1) ^ ((1 << (turn + 1)) - 1)):
        return True
    return bool(r[5] & ((1 << min(left, 64)) - 2))


class RuleSet:
    """The compiled rules of one RoundPlan, applied by the Python search (policy_search) and by ``evaluate``; the native
    core takes the same table (GakumasCLI.native.native_search.rule_table)."""

    def __init__(self, rules, state=None):
        self.rules = [dict(r) for r in rules or ()]
        self.root = RuleState.from_dict(state) if isinstance(state, dict) else (state or RuleState()).copy()
        rows = []
        for r in self.rules:
            keys = r.get("keys") or []
            exact = frozenset((str(k), int(u)) for k, u in keys if int(u) >= 0)
            anyup = frozenset(str(k) for k, u in keys if int(u) < 0)
            rows.append((MODE[r["mode"]], TARGET[r["target"]], bool(keys), exact, int(r.get("abs", 0)),
                         int(r.get("left", 0)), int(r.get("n", 0)), r.get("field") or "", r.get("op") or ">=",
                         int(r.get("value", 0)), float(r.get("weight", 0.0)), int(r.get("penalty", 0)), anyup))
        self._rows = rows
        self.has_prefer = any(x[0] == _M_PREFER for x in rows)
        self.has_milestones = any(x[0] in (_M_MSH, _M_MSS, _M_MSE) for x in rows)

    def __bool__(self):
        return bool(self._rows)

    def to_job(self):
        return {"rules": self.rules, "state": self.root.as_dict()}

    @classmethod
    def from_job(cls, data):
        if not data:
            return None
        return cls(data.get("rules") or (), data.get("state"))

    def with_state(self, state):
        rs = RuleSet.__new__(RuleSet)
        rs.rules, rs._rows, rs.has_prefer, rs.has_milestones = self.rules, self._rows, self.has_prefer, self.has_milestones
        rs.root = state.copy()
        return rs

    def start(self):
        return self.root.copy()

    @staticmethod
    def _match(row, node, action):
        target = row[1]
        kind = action[0]
        if target == _T_DRINK:
            if kind != "drink":
                return False
            if not row[2]:
                return True
            did = node.drinks[int(action[1])]
            return did in row[12]
        if target == _T_PLAY:
            if kind != "play":
                return False
            if not row[2]:
                return True
            card = node.deck.hand[int(action[1])]
            cid = card["produce_card_id"]
            return cid in row[12] or (cid, int(card.get("current_upgrade_count", 0) or 0)) in row[3]
        if target == _T_END:
            return kind == "end"
        return False

    def sync(self, rs, node):
        turn = int(node.turn)
        if rs.turn != turn:
            rs.turn, rs.acts, rs.plays = turn, 0, 0

    def filter(self, rs, node, actions):
        """The allowed subset of ``actions`` (order kept); core_rules.h rt_filter."""
        turn, left = _turn_left(node)
        n = len(actions)
        keep = [True] * n
        rows = self._rows
        for row in rows:
            if row[0] == _M_FORBID and _active(row, turn, left):
                for i, a in enumerate(actions):
                    if keep[i] and self._match(row, node, a):
                        keep[i] = False
            elif row[0] == _M_NOSKIP and _active(row, turn, left):
                for i, a in enumerate(actions):
                    if a[0] == "end":
                        keep[i] = False
        for row in rows:
            if row[0] == _M_CAP and _active(row, turn, left) and rs.plays >= row[6]:
                for i, a in enumerate(actions):
                    if a[0] == "play":
                        keep[i] = False
        if rs.acts == 0:
            for row in rows:
                if row[0] == _M_FIRST and _active(row, turn, left):
                    hit = [i for i, a in enumerate(actions) if keep[i] and self._match(row, node, a)]
                    if hit:
                        keep = [False] * n
                        for i in hit:
                            keep[i] = True
                        break
        for k, row in enumerate(rows):
            if row[0] == _M_REQUIRE and not (rs.done >> k) & 1 and _active(row, turn, left) \
                    and not _later(row, turn, left):
                hit = [i for i, a in enumerate(actions) if keep[i] and self._match(row, node, a)]
                if hit:
                    new = [False] * n
                    for i in hit:
                        new[i] = True
                    if row[1] == _T_PLAY:
                        for i, a in enumerate(actions):
                            if keep[i] and a[0] == "drink":
                                new[i] = True
                    keep = new
                    break
        if not any(keep):
            return list(actions)
        return [a for a, k in zip(actions, keep) if k]

    def prefer(self, node, action):
        """The summed prefer weight of ``action`` here (0.0 when none applies)."""
        turn, left = _turn_left(node)
        w = 0.0
        for row in self._rows:
            if row[0] == _M_PREFER and _active(row, turn, left) and self._match(row, node, action):
                w = w + row[10]
        return w

    def taken(self, rs, node, action):
        """Before ``node.step(action)``: the done bits and the turn's counters."""
        turn, left = _turn_left(node)
        for k, row in enumerate(self._rows):
            if row[0] == _M_REQUIRE:
                if _active(row, turn, left) and self._match(row, node, action):
                    rs.done |= 1 << k
            elif row[0] == _M_FIRST:
                if rs.acts == 0 and _active(row, turn, left) and self._match(row, node, action):
                    rs.done |= 1 << k
        rs.acts += 1
        if action[0] == "play":
            rs.plays += 1
        return turn, left

    def after(self, rs, node, turn, left):
        """After the step: milestones of the turn that just ended (``turn`` / ``left`` before the step)."""
        if not self.has_milestones or (not node.finished and int(node.turn) == turn):
            return
        st = node.state
        for k, row in enumerate(self._rows):
            if row[0] == _M_MSE:
                if not node.finished:
                    continue
            elif row[0] not in (_M_MSH, _M_MSS) or not _active(row, turn, left):
                continue
            x = (milestone_value(node, row[7]) if row[7] in COUNT_FIELDS or row[7] in SHARE_FIELDS
                 else int(getattr(st, row[7], 0) or 0))
            short = max(0, row[9] - x) if row[8] == ">=" else max(0, x - row[9])
            if short > 0:
                if row[0] == _M_MSH:
                    rs.failed |= 1 << k
                else:
                    rs.pen = _sat_add(rs.pen, _weighted(row[10], short))

    def advance(self, rs, node, action):
        """Step ``node`` by ``action`` keeping ``rs``; selects are not decisions."""
        if action[0] == "select":
            node.state.select_index_queue.append(list(action[1]))
            return
        self.sync(rs, node)
        turn, left = self.taken(rs, node, action)
        node.step(tuple(action), observe=False)
        self.after(rs, node, turn, left)

    def penalty(self, rs):
        """The soft penalty (the minor key's, core_rules.h rt_penalty)."""
        return rs.pen

    def major(self, rs, node):
        """The major key of a line ending at ``node`` (core_rules.h rt_major): unmet hard rules, then the goal's
        shortfall once the audition is over."""
        p = 0
        for k, row in enumerate(self._rows):
            if row[0] in (_M_REQUIRE, _M_FIRST) and not (rs.done >> k) & 1:
                p = _sat_add(p, row[11])
            elif row[0] == _M_MSH and (rs.failed >> k) & 1:
                p = _sat_add(p, row[11])
            elif row[0] == _M_MSG and node.finished:
                x = (milestone_value(node, row[7]) if row[7] in COUNT_FIELDS or row[7] in SHARE_FIELDS
                     else int(getattr(node.state, row[7], 0) or 0))
                short = row[9] - x if row[8] == ">=" else x - row[9]
                if short > 0:
                    p = _sat_add(p, _weighted(row[10], short))
        return p

    def objective(self, rs, node):
        """What ranks a finished line: score - soft penalty - major x MAJOR_SCALE (the native search's (major, score)
        order, as one int)."""
        return int(node.state.score) - self.penalty(rs) - self.major(rs, node) * MAJOR_SCALE

    def unmet(self, rs):
        """Indices of the hard rules this line misses."""
        out = []
        for k, row in enumerate(self._rows):
            if (row[0] in (_M_REQUIRE, _M_FIRST) and not (rs.done >> k) & 1) or \
                    (row[0] == _M_MSH and (rs.failed >> k) & 1):
                out.append(k)
        return out

    def state_after(self, session, path, check=False):
        """The rule state after replaying ``path`` on a clone of ``session`` (from this set's root state)."""
        node = session.clone()
        rs = self.start()
        violations = 0
        for a in path:
            if node.finished:
                break
            if check and a[0] != "select":
                self.sync(rs, node)
                if tuple(a) not in self.filter(rs, node, sorted(node.legal_actions())):
                    violations += 1
            self.advance(rs, node, a)
        return (rs, node, violations) if check else (rs, node)

    def evaluate(self, session, path):
        """{score, objective, penalty, unmet, soft, violations} of ``path`` from ``session``."""
        rs, node, violations = self.state_after(session, path, check=True)
        soft = self.penalty(rs) if node.finished else 0
        major = (self.major(rs, node) if node.finished else 0) + VIOLATION * violations
        score = int(node.state.score)
        return {"score": score, "objective": score - soft - major * MAJOR_SCALE, "penalty": soft + major,
                "major": major, "unmet": self.unmet(rs) if node.finished else [], "soft": rs.pen,
                "finished": bool(node.finished), "violations": violations}


def rules_of(plan, state=None):
    """RuleSet of a RoundPlan (None when it has no rules)."""
    rules = (plan or {}).get("rules") or []
    return RuleSet(rules, state) if rules else None


def describe_rule(rule, limit=None):
    """One line of text for a compiled rule (the live page's strip, the trial table)."""
    if rule["mode"] in ("ms_end", "ms_goal") and rule.get("goal"):
        if rule.get("field") == "stamina":
            return "目标：结束时体力尽量多"
        return f"目标：分数 {rule['op']} {int(rule['value']):,}" + ("（超出部分不计）" if rule["op"] == "<=" else "")
    if rule["mode"] == "ms_end":
        key = str(rule.get("field") or "")[6:]
        return f"控分 {SPLIT_LABELS.get(key, key)} 占比 {rule['op']} {int(rule['value']) / 10:g}%"
    mode = {"forbid": "禁止", "require": "必须", "first": "首手", "prefer": "偏好", "cap": "上限",
            "no_skip": "不许跳过", "ms_hard": "里程碑", "ms_soft": "里程碑（软）"}.get(rule["mode"], rule["mode"])
    what = ", ".join(k for k, _ in rule.get("keys") or ()) or {"drink": "任意饮料", "play": "任意卡", "end": "结束回合",
                                                             "none": FIELD_LABELS.get(rule.get("field"), "")}[rule["target"]]
    if rule["mode"] == "cap":
        what = f"出牌 ≤ {rule['n']}"
    if rule["mode"] in ("ms_hard", "ms_soft"):
        what = f"{FIELD_LABELS.get(rule['field'], rule['field'])} {rule['op']} {rule['value']}"
    when = ""
    if limit:
        turns = mask_turns(rule["abs"], rule["left"], limit)
        if turns and len(turns) < limit:
            when = f" T{turns[0]}" + (f"–{turns[-1]}" if turns[-1] != turns[0] else "")
            if len(turns) != turns[-1] - turns[0] + 1:
                when += f"（{len(turns)} 回合）"
    extra = f" {rule['weight']:+g}" if rule["mode"] == "prefer" else ""
    return f"{mode} {what}{extra}{when}"


def strip(plan):
    """Per-turn marks of a RoundPlan for the live page: [{turn, marks: [..]}]."""
    limit = int((plan or {}).get("limit_turn") or 0)
    cells = [{"turn": t, "marks": []} for t in range(1, limit + 1)]
    sym = {"forbid": "⛔", "require": "●", "first": "①", "prefer": "★", "cap": "⏹", "no_skip": "⏩",
           "ms_hard": "◇", "ms_soft": "◇"}
    for k, r in enumerate((plan or {}).get("rules") or ()):
        if r["mode"] == "forbid" and r.get("keep_to"):
            continue
        for t in mask_turns(r["abs"], r["left"], limit):
            cells[t - 1]["marks"].append({"rule": k, "sym": sym.get(r["mode"], "?"), "text": describe_rule(r)})
    for r in (plan or {}).get("resolve") or ():
        for t in mask_turns(r["abs"], r["left"], limit):
            cells[t - 1]["marks"].append({"sym": "⏱", "text": f"深算 {r['seconds']:g}s"})
    ex = (plan or {}).get("exhaust") or {}
    for b in ex.get("branch_at") or ():
        for t in b["turns"]:
            if 1 <= t <= limit:
                cells[t - 1]["marks"].append({"sym": "⑂", "text": f"分支 {b['cap']}×{b['seconds']:g}s"})
    if ex.get("tail_turns"):
        for t in range(max(1, limit - int(ex["tail_turns"]) + 1), limit + 1):
            cells[t - 1]["marks"].append({"sym": "∑", "text": f"尾段 {ex['tail_turns']} 回合"})
    return cells


# ------------------------------------------------------------------ budget helpers
def wall_estimate(plan, *, default_open=180.0, default_runs=3, default_tail=20.0):
    """Rough wall seconds of the opening solve under ``plan`` (the editor's warning)."""
    b = (plan or {}).get("budget") or {}
    runs = int(b.get("open_runs", default_runs))
    secs = float(b.get("open_seconds", default_open)) + float(b.get("open_tail_seconds", default_tail))
    alns = int(b.get("alns_rounds", 0)) * (float(b.get("alns_job_seconds", 15.0)) + 3.0)
    return runs * secs + alns


# ------------------------------------------------------------------ route -> rules ("抄作业")
def rules_from_route(session, route, picks):
    """Stage entries made from a route: ``picks`` are indices into ``route`` (select entries ignored)."""
    node = session.clone()
    out = {"drinks": [], "plays": []}
    wanted = set(int(i) for i in picks or ())
    last_turn, first = None, True
    for i, a in enumerate(route):
        a = tuple(a) if a[0] != "select" else ("select", a[1])
        if node.finished:
            break
        if a[0] == "select":
            node.state.select_index_queue.append(list(a[1]))
            continue
        turn = int(node.turn)
        if turn != last_turn:
            last_turn, first = turn, True
        if i in wanted:
            if a[0] == "drink":
                out["drinks"].append({"drink": node.drinks[int(a[1])], "rule": "require", "turns": [turn, turn]})
            elif a[0] == "play":
                card = node.deck.hand[int(a[1])]
                entry = {"card": card["produce_card_id"], "upgrade": int(card.get("current_upgrade_count", 0) or 0),
                         "turns": [turn, turn]}
                if first:
                    entry["rule"] = "first"
                else:
                    entry.update(rule="prefer", weight=2)
                out["plays"].append(entry)
        node.step(a, observe=False)
        first = False
    return out


def route_rows(session, route):
    """[{index, turn, kind, id, upgrade, text}] of a route, for the 抄作业 picker."""
    node = session.clone()
    rows = []
    for i, a in enumerate(route):
        if node.finished:
            break
        if a[0] == "select":
            node.state.select_index_queue.append(list(a[1]))
            continue
        a = tuple(a)
        row = {"index": i, "turn": int(node.turn), "kind": a[0]}
        if a[0] == "drink":
            row["id"] = node.drinks[int(a[1])]
        elif a[0] == "play":
            card = node.deck.hand[int(a[1])]
            row["id"], row["upgrade"] = card["produce_card_id"], int(card.get("current_upgrade_count", 0) or 0)
        rows.append(row)
        node.step(a, observe=False)
    return rows


# ------------------------------------------------------------------ presets
PRESETS = [
    {"format": FORMAT, "id": "preset_fast", "name": "快手", "note": "开局 60 s × 1 次，重算 10 s",
     "algorithm": "inherit", "stages": {"*": {"budget": {"open_seconds": 60, "open_runs": 1, "resolve_seconds": 10,
                                                          "quick_seconds": 8}}}},
    {"format": FORMAT, "id": "preset_deep", "name": "深算", "note": "开局 600 s × 2 次，ALNS 10 轮，第 6 回合开头深算 90 s",
     "algorithm": "inherit", "stages": {"*": {"budget": {"open_seconds": 600, "open_runs": 2, "alns_rounds": 10},
                                              "resolve": [{"turns": [6, 6], "seconds": 90}]}}},
    {"format": FORMAT, "id": "preset_drinks_late", "name": "饮料全压后", "note": "最后 3 回合以前不喝饮料",
     "algorithm": "inherit", "stages": {"*": {"drinks": [{"drink": "*", "rule": "forbid",
                                                          "turns": {"from_end": [4, 60]}}]}}},
    {"format": FORMAT, "id": "preset_open_exhaust", "name": "开局穷举", "note": "根分支打分（scored），尾段精确 3 回合",
     "algorithm": "inherit", "stages": {"*": {"exhaust": {"root": "scored", "tail_turns": 3, "tail_seconds": 30}}}},
    {"format": FORMAT, "id": "preset_qingzhi", "name": "青汁定时", "note": "初星スペシャル青汁 逐回合试喝，出一张表",
     "algorithm": "inherit", "stages": {"*": {"exhaust": {"sweep": [{"drink": "pdrink_00-3-005", "seconds": 10}]}}}},
]


def preset(pid):
    for p in PRESETS:
        if p["id"] == pid:
            return copy.deepcopy(p)
    return None
