# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""what of the current master the native core has no branch for.

    python -B gkms.py native-coverage [--json <out.json>]
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
NATIVE = ENGINE / "native"
TABLES = ENGINE / "ro" / "master" / "tables"


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _body(text, head):
    """The brace body of the function whose definition starts with ``head``."""
    start = text.index(head)
    i = text.index("{", start)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    raise ValueError(head)


def _top_cases(body, switch):
    """``case N:`` labels directly inside the first ``switch``, not in nested switches."""
    k = body.index(switch)
    i = body.index("{", k)
    depth, out = 0, set()
    pos = i
    for m in re.finditer(r"[{}]|\bcase\s+(-?\d+)\s*:", body[i:]):
        tok = m.group(0)
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
            if depth == 0:
                break
        elif depth == 1:
            out.add(int(m.group(1)))
    return out


def core_effect_types():
    session = _strip_comments((NATIVE / "core_session.h").read_text(encoding="utf-8"))
    exam = _strip_comments((NATIVE / "core_exam.h").read_text(encoding="utf-8"))
    body = _body(session, "GK_OUTLINE void apply_effect_body(Core *c, const Eff *e, int64_t permil)")
    light = _body(exam, "GK_GUARD int64_t apply_effect_light(Core *c, const Eff *e) {")
    return _top_cases(body, "switch (t)") | _top_cases(light, "switch (e->type)")


def core_grow_types():
    from GakumasCLI.engine import card_data as effects
    text = _strip_comments((NATIVE / "core_base.h").read_text(encoding="utf-8"))
    a = text.index("int t = g->type, sign = 0, tgt;")
    b = text.index("GK_OUTLINE void refresh_resolve", a)
    out = {int(x) for x in re.findall(r"\bt == (\d+)", text[a:b])}
    return out | set(effects._GROW_TO_EFFECT_TYPE) | set(effects._GROW_TO_EFFECT_TURN) | set(effects._GROW_TO_STATUS_COST)


def python_grow_types():
    from GakumasCLI.engine import card_data as effects
    tree = ast.parse((ENGINE / "engine" / "card_data.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "resolve_card_customizes")
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "grow_type":
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant):
                    out.add(int(comp.value))
                elif isinstance(op, ast.In) and isinstance(comp, ast.Name):
                    out.update(int(k) for k in getattr(effects, comp.id))
    return out


# reachable types the core refuses and why that is accepted for now: (kind, type) -> the content using it
KNOWN_REFUSED = {
    ("grow", 2): "pitem_03-3-088-challenge 強化ダンベル（桃） (a challenge P item, libraryHidden): its enchant "
                 "enchant-pitem_03-3-088-challenge-enc01 adds g_effect-lesson_reduce-2 (LESSON_REDUCE) to the deck",
}


def reachable_ids(tables=None):
    """Ids of intermediate-table rows that content rows reach (through any chain of references)."""
    from GakumasCLI.scenario.plan_support import reach_split
    return reach_split(tables or TABLES)


def _rows(table):
    with open(TABLES / f"{table}.jsonl", encoding="utf-8") as fh:
        return [json.loads(line).get("decoded") or {} for line in fh if line.strip()]


def _by_type(table):
    out = defaultdict(list)
    for row in _rows(table):
        t = row.get("effectType")
        if isinstance(t, dict) and t.get("number") is not None:
            out[int(t["number"])].append(row.get("id"))
    return out


def report():
    from GakumasCLI.native.gen_tables import CORE_EFFECT_TYPES
    from GakumasCLI.native.diffrun import PYTHON_ONLY_CARDS
    py_eff, core_eff = set(CORE_EFFECT_TYPES), core_effect_types()
    py_grow, core_grow = python_grow_types(), core_grow_types()
    out = {"effects": [], "grows": [], "cards": [], "neither": [], "refused": [], "deferred": []}
    reach, deferred = reachable_ids()

    def no_branch(kind, t, ids):
        used = [i for i in ids if i in reach]
        later = [i for i in ids if i in deferred]
        row = {"kind": kind, "type": t, "rows": len(ids), "example": (used or later or ids)[0], "reachable": len(used)}
        if used:
            row["known"] = (kind, t) in KNOWN_REFUSED
            out["refused"].append(row)
        elif later:
            row["deferred"] = len(later)
            out["deferred"].append(row)
        else:
            out["neither"].append(row)

    for t, ids in sorted(_by_type("ProduceExamEffect").items()):
        if t in py_eff and t not in core_eff:
            out["effects"].append({"type": t, "rows": len(ids), "example": ids[0]})
        elif t not in core_eff:
            no_branch("effect", t, ids)
    for t, ids in sorted(_by_type("ProduceCardGrowEffect").items()):
        if t in py_grow and t not in core_grow:
            out["grows"].append({"type": t, "rows": len(ids), "example": ids[0]})
        elif t not in core_grow:
            no_branch("grow", t, ids)
    out["cards"] = [{"card": k, "why": v} for k, v in sorted(PYTHON_ONLY_CARDS.items())]
    out["missing"] = len(out["effects"]) + len(out["grows"]) + len(out["cards"])
    out["gate_fail"] = out["missing"] + sum(1 for r in out["refused"] if not r["known"])
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    rep = report()
    for e in rep["effects"]:
        print(f"EFFECT {e['type']} rows {e['rows']} e.g. {e['example']}")
    for g in rep["grows"]:
        print(f"GROW {g['type']} rows {g['rows']} e.g. {g['example']}")
    for c in rep["cards"]:
        print(f"CARD {c['card']} ({c['why']})")
    for r in rep["refused"]:
        print(f"REFUSED {r['kind']} {r['type']} rows {r['rows']} reachable {r['reachable']} e.g. {r['example']}"
              + (" (known)" if r["known"] else ""))
    for d in rep["deferred"]:
        print(f"DEFERRED no branch, only unsupported content (シンクロ ...) reaches it, refused at a round's opening: "
              f"{d['kind']} {d['type']} rows {d['rows']} reachable {d['deferred']} e.g. {d['example']}")
    for n in rep["neither"]:
        print(f"INFO no branch, no content reaches it: {n['kind']} {n['type']} rows {n['rows']} e.g. {n['example']}")
    print(f"MISSING {rep['missing']} (effect types {len(rep['effects'])}, grow types {len(rep['grows'])}, "
          f"python-only cards {len(rep['cards'])})")
    print("GATE OK" if not rep["gate_fail"] else f"GATE FAIL {rep['gate_fail']}")
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if rep["gate_fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
