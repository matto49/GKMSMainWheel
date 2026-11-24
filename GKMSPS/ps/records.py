# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The account warehouses of the web simulator: idol cards, support cards, H.I.F memories and the 选拔试验メモリー a 選抜 leaves,
each record tagged with where it came from.
"""
from __future__ import annotations

import re

from GKMSPS.ps.data import data

KINDS = ("idol", "support", "memory", "selection")
SOURCES = ("imported", "custom", "simulated")
MAX_MEMORIES = 4
HIF_TAG = "hatsuboshi_idol_festival"


class RecordError(ValueError):
    pass


def _int(v, lo, hi, what):
    """An integer field; an enum's name ending in its number (IDOL_CARD_LEVEL_LIMIT_RANK_6, as the User/Get export
    writes enum fields) counts as that number."""
    if isinstance(v, dict) and "number" in v:
        v = v["number"]
    if isinstance(v, str) and not v.strip().lstrip("-").isdigit():
        m = re.search(r"_(\d+)$", v.strip())
        v = m.group(1) if m else v
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise RecordError(f"{what} 不是整数")
    if not lo <= n <= hi:
        raise RecordError(f"{what} 要在 {lo}–{hi} 之间（现在是 {n}）")
    return n


PRIMA_STELLA_RANK = 6
PRIMA_STELLA_DEARNESS = 37


def idol_limits(idol_id):
    d = data()
    row = d.idols.cards.get(idol_id)
    if row is None:
        raise RecordError(f"主数据里没有偶像卡 {idol_id}")
    ll = int((row.get("maxIdolCardLevelLimitRank") or {}).get("number") or 6)
    pid = row.get("idolCardPotentialId")
    pot = max([r["rank"] for r in d.idols.potential if r["id"] == pid] or [4])
    return ll, pot


def support_limits(support_id):
    d = data()
    row = d.support_cards.get(support_id)
    if row is None:
        raise RecordError(f"主数据里没有支援卡 {support_id}")
    rarity = str(row.get("rarity", {}).get("name") if isinstance(row.get("rarity"), dict) else row.get("rarity") or "")
    top = {"SUPPORT_CARD_RARITY_R": 40, "SUPPORT_CARD_RARITY_SR": 50, "SUPPORT_CARD_RARITY_SSR": 60}.get(rarity, 60)
    return top


def idol(doc):
    """{id, level_limit_rank (特訓), potential_rank (才能開花), prima_stella}; プリマステラ needs a card that has one
    and 特訓 6 or more (Setting.idolCardPrimaStellaLevelLimitRank)."""
    if not isinstance(doc, dict):
        raise RecordError("偶像卡记录要是对象")
    iid = str(doc.get("id") or doc.get("idol_card_id") or "")
    ll, pot = idol_limits(iid)
    out = {"id": iid,
           "level_limit_rank": _int(doc.get("level_limit_rank", ll), 0, ll, "偶像卡特訓"),
           "potential_rank": _int(doc.get("potential_rank", 1), 0, pot, "偶像卡才能開花")}
    if doc.get("prima_stella") or doc.get("is_prima_stella"):
        if not data().prima_card(iid):
            raise RecordError("这张偶像卡没有プリマステラ")
        if out["level_limit_rank"] < PRIMA_STELLA_RANK:
            raise RecordError(f"プリマステラ要特訓 {PRIMA_STELLA_RANK} 以上")
        out["prima_stella"] = True
    return out


def support(doc):
    """{id, level, limit_rank}"""
    if not isinstance(doc, dict):
        raise RecordError("支援卡记录要是对象")
    sid = str(doc.get("id") or doc.get("support_card_id") or "")
    top = support_limits(sid)
    return {"id": sid, "level": _int(doc.get("level", top), 1, top, "支援卡等级"),
            "limit_rank": _int(doc.get("limit_rank", doc.get("level_limit_rank", 4)), 0, 4, "支援卡突破")}


PLAN_NUMBER = {"COMMON": 1, "PLAN1": 2, "PLAN2": 3, "PLAN3": 4}


def _plan_type(v):
    """The memory's plan as ProducePlanType's number; the User/Get inventory sends the enum's name (the 500 on
    「从离线账号库存导入」), the page sends a number or PLAN1."""
    if v is None or v == "":
        return 0
    if isinstance(v, bool):
        raise RecordError("回忆的培育方针不对")
    if isinstance(v, int):
        return v
    t = str(v).strip()
    if t.isdigit():
        return int(t)
    t = t.rsplit("PRODUCE_PLAN_TYPE_", 1)[-1].upper()
    if t not in PLAN_NUMBER:
        raise RecordError(f"回忆的培育方针不对：{v}")
    return PLAN_NUMBER[t]


def _grade(v):
    """The memory's grade as shown in a list: a number, or the enum's tail when the inventory sends a name."""
    if v is None or v == "":
        return 0
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    t = str(v).strip()
    if t.lstrip("-").isdigit():
        return int(t)
    for pre in ("PRODUCE_MEMORY_GRADE_", "MEMORY_GRADE_", "GRADE_"):
        if t.startswith(pre):
            return t[len(pre):]
    return t[:16]


def memory(doc):
    """The H.I.F memory as the simulator reads it: the idol it came from, its exam card (id, upgrade, customizes,
    phase), its abilities, and the numbers shown in a list (grade, parameters)."""
    if not isinstance(doc, dict):
        raise RecordError("回忆记录要是对象")
    d = data()
    pc = doc.get("produce_card") or {}
    cid = str(pc.get("id") or "")
    if cid and (cid, 0) not in d.cards:
        raise RecordError(f"主数据里没有技能卡 {cid}")
    up = _int(pc.get("upgrade_count", 0), 0, 3, "回忆技能卡强化") if cid else 0
    cz = []
    for x in pc.get("customizes") or ():
        zid = str((x or {}).get("id") or "")
        if zid not in d.customizes:
            raise RecordError(f"主数据里没有定制 {zid}")
        cz.append({"id": zid, "customize_count": _int(x.get("customize_count", 1), 1, 3, "定制级数")})
    abilities = []
    for ab in doc.get("abilities") or ():
        aid = str((ab or {}).get("id") or "")
        lv = _int(ab.get("level", 1), 1, 9, "回忆能力等级")
        if d.M.memory_skill(aid, lv) is None:
            raise RecordError(f"主数据里没有回忆能力 {aid} Lv{lv}")
        abilities.append({"id": aid, "level": lv})
    ids = [a["id"] for a in abilities]
    hif = [a for a in ids if HIF_TAG in a]
    kinds = [ability_kind(a) for a in ids if HIF_TAG not in a]
    if len(set(ids)) < len(ids):
        raise RecordError("同一个能力不能有两个")
    if len(hif) > 1:
        raise RecordError("一个回忆最多 1 个 H.I.F 能力")
    if len(set(kinds)) < len(kinds):
        raise RecordError("同一种普通能力不能有两个（不同等级也算同一种）")
    if hif and len(kinds) > 3:
        raise RecordError("H.I.F 回忆是 3 个普通能力 + 1 个 H.I.F 能力")
    if len(abilities) > 5:
        raise RecordError("回忆能力最多 5 个")
    out = {"idol_card_id": str(doc.get("idol_card_id") or ""), "character_id": str(doc.get("character_id") or ""),
           "plan_type": _plan_type(doc.get("plan_type")), "grade": _grade(doc.get("grade")),
           "produce_card": {"id": cid, "upgrade_count": up, "customizes": cz} if cid else None,
           "produce_card_phase_type": _int(doc.get("produce_card_phase_type", 1), 1, 2, "回忆技能卡阶段"),
           "abilities": abilities}
    for k in ("vocal", "dance", "visual", "stamina", "power"):
        if doc.get(k) is not None:
            out[k] = int(doc[k])
    if out["idol_card_id"] and out["idol_card_id"] in d.idols.cards and not out["character_id"]:
        out["character_id"] = d.idols.character(out["idol_card_id"])
    return out


def is_hif_memory(doc):
    return any(HIF_TAG in str(ab.get("id")) for ab in doc.get("abilities") or ())


TIER_TAIL = re.compile(r"-(\d\d)-\d\d\d(?:-\d\d\d)?$")
ATTR_ZH = {"VOCAL": "Vo", "DANCE": "Da", "VISUAL": "Vi"}


def ability_kind(aid):
    """An ordinary ability's kind: its id without the tier part (``…-03-001-001`` / ``…-03-001``)."""
    tail = str(aid).split("p_memory_skill-")[-1]
    return TIER_TAIL.sub("", tail[len("common-"):] if tail.startswith("common-") else tail)


def hif_plan(aid):
    """The plan (\"1\" sense, \"2\" logic, \"3\" anomaly) of an H.I.F ability: that of the card its enchant names."""
    d = data()
    sk = d.M.memory_skill(aid)
    ench = d.effect(sk.effect_id).get("produceExamStatusEnchantId") if sk else ""
    m = re.search(r"p_card-(\d\d)-", ench or "")
    return m.group(1)[-1] if m else None


def ability_text(aid, level=1):
    """An ordinary memory ability in a few Chinese words (Vo 初始值 +20, Da 成长率 +2.8%, 初始 P 点 +40,
    Vi 课后 45% 回复体力 4), or None for one this does not know."""
    d = data()
    sk = d.M.memory_skill(aid, level)
    if sk is None:
        return None
    et, v = sk.effect_type or "", (sk.value or (None,))[0]
    attr = next((z for k, z in ATTR_ZH.items() if et.startswith(k + "_")), None)
    if attr and et.endswith("_GROWTH_RATE_ADDITION"):
        return f"{attr} 成长率 +{(v or 0) / 10:g}%"
    if attr and et.endswith("_ADDITION"):
        return f"{attr} 初始值 +{v}"
    if et == "PRODUCE_POINT_ADDITION_DISABLE_TRIGGER":
        return f"初始 P 点 +{v}"
    if et == "STAMINA_RECOVER_FIX":
        a = {"vocal": "Vo", "dance": "Da", "visual": "Vi"}.get((sk.cond or {}).get("lesson_attr"), "")
        return f"{a} 课后 {sk.rate_permil / 10:g}% 回复体力 {v}".strip()
    return None


def normalize(kind, doc):
    if kind == "idol":
        return idol(doc)
    if kind == "support":
        return support(doc)
    if kind == "memory":
        return memory(doc)
    if kind == "selection":
        if not isinstance(doc, dict) or doc.get("format") != "gkms.ps.selection_memory.v1":
            raise RecordError("不是选拔试验メモリー")
        return doc
    raise RecordError(f"没有 {kind} 这种仓库")


def record_name(kind, doc):
    d = data()
    if kind in ("idol", "selection"):
        iid = doc["id"] if kind == "idol" else doc["idol"]["id"]
        row = d.idols.cards.get(iid) or {}
        return str(row.get("name") or iid)
    if kind == "support":
        return str((d.support_cards.get(doc["id"]) or {}).get("name") or doc["id"])
    pc = doc.get("produce_card") or {}
    return str((d.card(pc.get("id")) or {}).get("name") or doc.get("idol_card_id") or "回忆")


def from_inventory(inv):
    """Records from a User/Get inventory (``user_idol_card_list`` / ``user_support_card_list`` /
    ``user_memory_list``); rows the master cannot build are counted, not raised."""
    out, skipped = [], 0
    for row in inv.get("user_idol_card_list") or ():
        try:
            prima = (bool(row.get("is_prima_stella")) and bool(data().prima_card(str(row.get("idol_card_id") or "")))
                     and int(row.get("level_limit_rank") or 0) >= PRIMA_STELLA_RANK)
            out.append(("idol", idol({"id": row.get("idol_card_id"),
                                      "level_limit_rank": row.get("level_limit_rank", 0),
                                      "potential_rank": row.get("potential_rank", 0), "prima_stella": prima})))
        except (RecordError, ValueError, TypeError, KeyError):
            skipped += 1
    for row in inv.get("user_support_card_list") or ():
        try:
            out.append(("support", support({"id": row.get("support_card_id"), "level": row.get("level", 1),
                                            "limit_rank": row.get("level_limit_rank", row.get("limit_rank", 0))})))
        except (RecordError, ValueError, TypeError, KeyError):
            skipped += 1
    for row in inv.get("user_memory_list") or ():
        try:
            m = memory(row)
        except (RecordError, ValueError, TypeError, KeyError):
            skipped += 1
            continue
        if is_hif_memory(m):
            out.append(("memory", m))
    return out, skipped


def formation(doc):
    """The formation the page sends: idol, 6 supports (one may be rental), up to 4 memories, account settings."""
    if not isinstance(doc, dict):
        raise RecordError("编成要是对象")
    d = data()
    out = {"idol": idol(doc.get("idol") or {})}
    sups = doc.get("supports") or []
    if len(sups) != 6:
        raise RecordError("支援卡要 6 张")
    seen = set()
    out["supports"] = []
    for s in sups:
        x = support(s or {})
        if x["id"] in seen:
            raise RecordError("同一张支援卡不能编两次")
        seen.add(x["id"])
        x["rental"] = bool((s or {}).get("rental"))
        out["supports"].append(x)
    if sum(1 for x in out["supports"] if x["rental"]) > 1:
        raise RecordError("租借支援卡最多 1 张")
    mems = [m for m in doc.get("memories") or [] if m]
    if len(mems) > MAX_MEMORIES:
        raise RecordError(f"回忆最多 {MAX_MEMORIES} 个")
    out["memories"] = [memory(m) for m in mems]
    acc = doc.get("account") or {}
    base = d.default_account()
    a = {"producer_level": _int(acc.get("producer_level", base["producer_level"]), 1, base["producer_level"], "P 等级"),
         "dearness_level": _int(acc.get("dearness_level", base["dearness_level"]), 0, base["dearness_level"], "亲爱度"),
         "true_ends": [t for t in base["true_ends"] if t in (acc.get("true_ends", base["true_ends"]) or ())],
         "growth_panel": {}}
    panel = acc.get("growth_panel") or {}
    for sheet, top in base["growth_panel"].items():
        a["growth_panel"][sheet] = _int(panel.get(sheet, top), 0, top, f"成长面板 {sheet[-2:]}")
    out["account"] = a
    if out["idol"].get("prima_stella") and a["dearness_level"] < PRIMA_STELLA_DEARNESS:
        raise RecordError(f"プリマステラ要亲爱度 {PRIMA_STELLA_DEARNESS}")
    return out
