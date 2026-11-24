# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""what the exam core was built against, and the opening check that refuses a round carrying anything else."""
from __future__ import annotations

import json
from pathlib import Path

KNOWN_PATH = Path(__file__).resolve().parents[1] / "engine" / "data" / "core_known_enums.json"
ENUMS_PATH = Path(__file__).resolve().parents[1] / "engine" / "data" / "exam_enums.json"

SUPPORTED_PLANS = {1: "PRODUCE_PLAN_TYPE_COMMON", 2: "PRODUCE_PLAN_TYPE_PLAN1", 3: "PRODUCE_PLAN_TYPE_PLAN2",
                   4: "PRODUCE_PLAN_TYPE_PLAN3"}
PLAN4_NAME = "PRODUCE_PLAN_TYPE_PLAN4"
PLAN4_NUMBER = 5
PLAN_LABELS = {1: "通用", 2: "感性", 3: "理性", 4: "非凡", 5: "シンクロ"}
PLAN_LABELS_BY_NAME = {"PRODUCE_PLAN_TYPE_COMMON": "通用", "PRODUCE_PLAN_TYPE_PLAN1": "感性",
                       "PRODUCE_PLAN_TYPE_PLAN2": "理性", "PRODUCE_PLAN_TYPE_PLAN3": "非凡", PLAN4_NAME: "シンクロ"}
# ProduceCardPlan4GroupType 1-4: name, the display effect type, the colour
PLAN4_GROUPS = {1: ("リズム", 220, "#29FFD4"), 2: ("エコー", 221, "#FF95F6"),
                3: ("ハーモニー", 222, "#FFFC67"), 4: ("メロディー", 223, "#BEFE5A")}
SUPPORTED_CATEGORIES = {1, 2, 3}
NOTICE_PLAN4 = "シンクロ暂不支持：本局只记录，不推荐"
NOTICE_NEW = "新内容暂不支持：本局只记录，不推荐"

# Master attributes walked, the label a reason names a row by, and whether the row's fields are checked
TABLES = {"cards": "卡", "effects": "效果", "triggers": "触发", "enchants": "附魔", "searches": "检索",
          "customizes": "强化", "grow_effects": "成长", "card_status_enchants": "卡附魔", "items": "P道具",
          "item_effects": "道具效果", "drinks": "饮料", "drink_effects": "饮料效果"}
FIELD_CHECKED = ("effects", "triggers", "enchants", "searches", "grow_effects", "customizes")
_PLAN4_HINTS = ("plan4", "fusion", "metamorphose")
NON_RULE = frozenset({("items", "idolCardProduceItemType")})


def _skip(key):
    return key.endswith("Descriptions") or key.endswith("DescriptionIds")


def _walk(obj, path, enums, refs):
    """Enum values (path, number, name) and every other string below ``obj``; description subtrees are display."""
    if isinstance(obj, dict):
        if set(obj) == {"name", "number"} and isinstance(obj.get("name"), str):
            enums.append((path, obj["number"], obj["name"]))
            return
        for k, v in obj.items():
            if not _skip(k):
                _walk(v, f"{path}.{k}" if path else k, enums, refs)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, path + "[]", enums, refs)
    elif isinstance(obj, str):
        refs.add(obj)


def _rows_by_id(master):
    index = {}
    for table in TABLES:
        for key, row in (getattr(master, table, None) or {}).items():
            rid = key[0] if isinstance(key, tuple) else key
            if isinstance(rid, str):
                index.setdefault(rid, []).append((table, row))
    return index


def freeze(master, enums_path=ENUMS_PATH):
    """The reference file's content for ``master`` (run once, at the rules the core was built for)."""
    enums_doc = json.loads(Path(enums_path).read_text(encoding="utf-8"))
    by_name = {name: int(number) for values in enums_doc.values() if isinstance(values, dict)
               for number, name in values.items() if str(number).lstrip("-").isdigit()}
    type_of = {name: kind for kind, values in enums_doc.items() if isinstance(values, dict) for name in values.values()}
    paths, keys = {}, {}
    for table in TABLES:
        for row in (getattr(master, table, None) or {}).values():
            if table in FIELD_CHECKED:
                keys.setdefault(table, set()).update(k for k in row if not _skip(k))
            found = []
            _walk(row, "", found, set())
            for path, number, name in found:
                entry = paths.setdefault(table, {}).setdefault(path, set())
                if number is not None:
                    entry.add(int(number))
                kind = type_of.get(name)
                if kind:
                    entry.update(by_name[n] for n in enums_doc[kind].values() if n in by_name and by_name[n] > 0)
    for table in ("cards", "items", "drinks"):
        if "planType" in paths.get(table, {}):
            paths[table]["planType"] = set(SUPPORTED_PLANS)
    if "category" in paths.get("cards", {}):
        paths["cards"]["category"] = set(SUPPORTED_CATEGORIES)
    return {"note": "the enum numbers and fields the exam core (rules 62) was built against; extend only in "
                    "the commit that implements the new content (scenario/plan_support.py)",
            "enums": {t: {p: sorted(v) for p, v in sorted(d.items())} for t, d in sorted(paths.items())},
            "fields": {t: sorted(v) for t, v in sorted(keys.items())}}


_KNOWN = None


def known():
    global _KNOWN
    if _KNOWN is None:
        doc = json.loads(KNOWN_PATH.read_text(encoding="utf-8"))
        _KNOWN = ({t: {p: set(v) for p, v in d.items()} for t, d in doc["enums"].items()},
                  {t: set(v) for t, v in doc["fields"].items()})
    return _KNOWN


def _row_problems(table, row, label):
    """(is_plan4, text) for each thing in one row the core was not built against."""
    enums, fields = known()
    found = []
    _walk(row, "", found, set())
    out = []
    for path, number, name in found:
        if (table, path.split(".")[0].split("[")[0]) in NON_RULE:
            continue
        allowed = enums.get(table, {}).get(path)
        plan4 = any(h in path.lower() or h in str(name).lower() for h in _PLAN4_HINTS) or str(name) == PLAN4_NAME
        if allowed is None:
            out.append((plan4, f"{label} 带新字段 {path}"))
        elif number is None or int(number) not in allowed:
            if path == "planType":
                plan4 = plan4 or number == PLAN4_NUMBER
                out.append((plan4, f"{label} 是シンクロ" if plan4 else f"{label} 的流派 {number}（{name}）未支持"))
            elif path == "category":
                out.append((plan4, f"{label} 的类别 {number}（{name}）未支持"))
            else:
                out.append((plan4, f"{label} 的 {path} = {number}（{name}）未支持"))
    if table in FIELD_CHECKED:
        for key in row:
            if (table, key) in NON_RULE:
                continue
            if not _skip(key) and key not in fields.get(table, ()) and key not in {p.split(".")[0].split("[")[0]
                                                                                 for p, _n, _m in found}:
                out.append((any(h in key.lower() for h in _PLAN4_HINTS), f"{label} 带新字段 {key}"))
    return out


def _state(master):
    state = master.__dict__.get("_plan_support")
    if state is None:
        state = {"index": _rows_by_id(master), "memo": {}, "idols": None}
        master.__dict__["_plan_support"] = state
    return state


def root_problems(master, rid):
    """Problems of the row(s) with id ``rid`` (every upgrade of a card) and of every row they reach, memoized."""
    state = _state(master)
    if rid in state["memo"]:
        return state["memo"][rid]
    index = state["index"]
    seen, stack, out = set(), [rid], []
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in index:
            continue
        seen.add(cur)
        for table, row in index[cur]:
            label = f"{TABLES[table]} {cur}"
            for plan4, text in _row_problems(table, row, label):
                item = (plan4, text if cur == rid else f"{TABLES[index[rid][0][0]]} {rid} → {text}")
                if item not in out:
                    out.append(item)
            refs = set()
            _walk(row, "", [], refs)
            stack.extend(r for r in refs if r in index and r not in seen)
    state["memo"][rid] = out
    return out


def idol_plan(master, idol_card_id):
    """(number, name) of IdolCard.planType, or None (no IdolCard table / row)."""
    state = _state(master)
    if state["idols"] is None:
        state["idols"] = {}
        path = Path(master.master_dir or "") / "IdolCard.jsonl"
        if master.master_dir is not None and path.exists():
            for raw in path.read_text(encoding="utf-8").splitlines():
                row = (json.loads(raw).get("decoded") or {}) if raw.strip() else {}
                plan = row.get("planType")
                if isinstance(plan, dict) and isinstance(row.get("id"), str):
                    state["idols"][row["id"]] = (plan.get("number"), plan.get("name"))
    return state["idols"].get(str(idol_card_id or ""))


def loadout_problems(master, doc):
    """Every (is_plan4, text) problem of a normalized loadout document; [] when the core can run it."""
    out = []
    plan = idol_plan(master, doc.get("idol_card_id"))
    if plan is not None and plan[0] not in known()[0].get("cards", {}).get("planType", SUPPORTED_PLANS):
        plan4 = plan[0] == PLAN4_NUMBER or plan[1] == PLAN4_NAME
        out.append((plan4, f"偶像 {doc.get('idol_card_id')} 是シンクロ" if plan4
                    else f"偶像 {doc.get('idol_card_id')} 的流派 {plan[0]}（{plan[1]}）未支持"))
    roots = [c.get("produce_card_id") for c in list(doc.get("deck_cards") or ()) + list(doc.get("produce_cards") or ())
             if isinstance(c, dict)]
    roots += list(doc.get("produce_item_ids") or ()) + list(doc.get("drink_ids") or ())
    roots += list(doc.get("lesson_start_enchant_ids") or ()) + list(doc.get("main_item_enchant_ids") or ())
    for rid in roots:
        if isinstance(rid, str):
            for item in root_problems(master, rid):
                if item not in out:
                    out.append(item)
    return out


def refusal(problems):
    """The panel's notice for ``problems`` (loadout_problems), or None."""
    if not problems:
        return None
    head = NOTICE_PLAN4 if any(p for p, _t in problems) else NOTICE_NEW
    texts = [t for _p, t in problems]
    more = f"等 {len(texts)} 项" if len(texts) > 2 else ""
    return f"{head}（{'；'.join(texts[:2])}{more}）"


def check_loadout(master, doc):
    """The refusal notice of a normalized loadout, or None."""
    return refusal(loadout_problems(master, doc))


def unsupported_row(row):
    """A content row of an unsupported plan or category (the master update's coverage lists these, not gating)."""
    plan, cat = row.get("planType"), row.get("category")
    pn = plan.get("number") if isinstance(plan, dict) else plan
    cn = cat.get("number") if isinstance(cat, dict) else None
    return (pn is not None and pn not in SUPPORTED_PLANS) or (cn is not None and cn not in SUPPORTED_CATEGORIES) \
        or bool(row.get("plan4GroupType"))


# tables whose rows are content a produce or an exam can hold are roots; these are reached only through references
INTERMEDIATE = frozenset({"ProduceExamEffect", "ProduceExamStatusEnchant", "ProduceExamTrigger", "ProduceCardGrowEffect",
                          "ProduceCardSearch", "ProduceDrinkEffect", "ProduceItemEffect", "ProduceEffect", "EffectGroup"})


def _strings(obj, out):
    """Every string below ``obj``, descriptions included (native_coverage's reach)."""
    if isinstance(obj, str):
        out.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _strings(v, out)
    return out


def reach_split(tables_dir):
    """(ids of intermediate rows supported content reaches, ids only unsupported content reaches): every reference
    chain from a content row (any table outside INTERMEDIATE) of ``tables_dir``; a content row is unsupported when
    unsupported_row says so.
    """
    rows, roots = {}, ([], [])
    for path in sorted(Path(tables_dir).glob("*.jsonl")):
        inter = path.stem in INTERMEDIATE
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line).get("decoded") or {}
                refs = _strings(row, set())
                rid = row.get("id")
                if inter and isinstance(rid, str):
                    rows.setdefault(rid, set()).update(refs - {rid})
                elif not inter:
                    roots[1 if unsupported_row(row) else 0].append(refs)

    def reach(root_refs):
        seen, stack = set(), [r for refs in root_refs for r in refs if r in rows]
        while stack:
            rid = stack.pop()
            if rid not in seen:
                seen.add(rid)
                stack.extend(r for r in rows[rid] if r in rows and r not in seen)
        return seen
    supported = reach(roots[0])
    return supported, reach(roots[1]) - supported


def plan_label(plan):
    """The plan's Chinese label from a number, an enum name or {name, number}; unknown -> the raw value."""
    if isinstance(plan, dict):
        return PLAN_LABELS_BY_NAME.get(plan.get("name")) or PLAN_LABELS.get(plan.get("number")) or str(plan.get("name"))
    if isinstance(plan, int):
        return PLAN_LABELS.get(plan, str(plan))
    return PLAN_LABELS_BY_NAME.get(str(plan), str(plan))


def main(argv=None):
    """python -B -m GakumasCLI.scenario.plan_support freeze: rewrite the reference from the current master."""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] != ["freeze"]:
        print("usage: python -B -m GakumasCLI.scenario.plan_support freeze")
        return 2
    from GakumasCLI.paths import MASTER_DIR
    from GakumasCLI.scenario.exam_session import Master
    doc = freeze(Master.load(MASTER_DIR))
    KNOWN_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(KNOWN_PATH, sum(len(v) for v in doc["enums"].values()), "enum paths,",
          sum(len(v) for v in doc["fields"].values()), "fields")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
