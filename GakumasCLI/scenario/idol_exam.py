# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""each idol's attribute order and every audition's 審査基準, from the master, for the page's idol bar.

    python -B -m GakumasCLI.scenario.idol_exam [--master DIR]        prints the table as JSON
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

SCHEMA = "gkms.idol_exam.v1"
ATTRS = ("vocal", "dance", "visual")
STEP = {16: "mid1", 17: "mid2", 18: "final"}
# produce -> (scenario, scenario label, difficulty label, {step: audition label})
PRODUCES = {
    "produce-001": ("hajime", "初", "レギュラー", {16: "中间", 18: "最终"}),
    "produce-002": ("hajime", "初", "プロ", {16: "中间", 18: "最终"}),
    "produce-003": ("hajime", "初", "マスター", {16: "中间", 18: "最终"}),
    "produce-006": ("hajime", "初", "レジェンド", {16: "中间", 18: "最终"}),
    "produce-004": ("nia", "N.I.A", "プロ", {16: "一次", 17: "二次", 18: "最终"}),
    "produce-005": ("nia", "N.I.A", "マスター", {16: "一次", 17: "二次", 18: "最终"}),
    "produce-007": ("hif", "H.I.F", "選抜", {16: "一次", 17: "二次", 18: "最终"}),
    "produce-008": ("hif", "H.I.F", "本戦", {16: "中间", 18: "最终"}),
}
SCENARIOS = (("hajime", "初"), ("nia", "N.I.A"), ("hif", "H.I.F"))
# the audition each scenario's chip shows until another one of that scenario is picked: its hardest final
HEADLINE = {"hajime": "produce-006:18:1", "nia": "produce-005:18:4", "hif": "produce-008:18:1"}
BALANCED_SECOND_SHARE = 0.72

FALLBACK_ORDER = {
    "hski": "ViDaVo", "ttmr": "VoDaVi", "fktn": "DaViVo", "atbm": "DaVoVi", "amao": "VoViDa", "kllj": "ViDaVo",
    "kcna": "DaViVo", "ssmk": "DaViVo", "shro": "VoDaVi", "jsna": "ViVoDa", "hmsz": "VoViDa", "hume": "DaVoVi",
    "hrnm": "ViDaVo",
}
_SHORT = {"Vo": "vocal", "Da": "dance", "Vi": "visual"}

_CACHE = {}


def _master_dir(master_dir=None):
    if master_dir is not None:
        return Path(master_dir)
    from GakumasCLI import paths as gkms_paths
    return Path(gkms_paths.MASTER_DIR)


def _rows(folder, name):
    path = folder / f"{name}.jsonl"
    if not path.is_file():
        return []
    out = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                out.append(row.get("decoded", row))
    return out


def exam_id(produce_id, step, number):
    return f"{produce_id}:{int(step)}:{int(number)}"


def _exam_label(produce_id, step, number, numbers):
    _scenario, name, difficulty, steps = PRODUCES[produce_id]
    label = f"{name} {difficulty} {steps.get(step, STEP.get(step, step))}"
    return f"{label} 第{number}档" if numbers > 1 else label


def build(master_dir=None):
    folder = _master_dir(master_dir)
    configs = {r["id"]: r for r in _rows(folder, "ProduceExamBattleConfig")}
    difficulty = defaultdict(list)
    for r in _rows(folder, "ProduceStepAuditionDifficulty"):
        difficulty[r["id"]].append(r)
    names = {r["id"]: f"{r.get('lastName', '')}{r.get('firstName', '')}" for r in _rows(folder, "Character")}
    cards = defaultdict(list)
    for card in _rows(folder, "IdolCard"):
        cards[card.get("characterId")].append(card)

    numbers = Counter()
    seen = set()
    idols = []
    for character in sorted(cards):
        votes = defaultdict(Counter)
        holders = defaultdict(lambda: defaultdict(list))
        for card in cards[character]:
            for r in difficulty.get(card.get("produceStepAuditionDifficultyId"), ()):
                cfg = configs.get(r.get("produceExamBattleConfigId"))
                step = (r.get("stepType") or {}).get("number") if isinstance(r.get("stepType"), dict) else r.get("stepType")
                if cfg is None or r.get("produceId") not in PRODUCES or step not in STEP:
                    continue
                key = (r["produceId"], step, int(r.get("number") or 1))
                line = (int(cfg.get("vocal") or 0), int(cfg.get("dance") or 0), int(cfg.get("visual") or 0),
                        int(r.get("rankThreshold") or 0), int(r.get("baseScore") or 0), int(r.get("parameterBaseLine") or 0))
                votes[key][line] += 1
                holders[key][line].append(card["id"])
        lines, orders = {}, Counter()
        for key in sorted(votes):
            line, _count = max(votes[key].items(), key=lambda kv: (kv[1], kv[0]))
            entry = {"vocal": line[0], "dance": line[1], "visual": line[2], "rank": line[3], "score": line[4],
                     "base_line": line[5]}
            others = sorted(c for other, ids in holders[key].items() if other[:3] != line[:3] for c in ids)
            if others:
                entry["differs"] = others
            lines[exam_id(*key)] = entry
            orders[tuple(sorted(ATTRS, key=lambda a: -entry[a]))] += 1
            seen.add(key)
        idol = {"id": character, "name": names.get(character) or character}
        if lines:
            order = max(orders.items(), key=lambda kv: kv[1])[0]
            ref = lines.get(HEADLINE["hif"]) or lines.get(HEADLINE["hajime"]) or next(iter(lines.values()))
            first, second = (ref[a] for a in order[:2])
            idol.update(order=list(order), source="master", one_order=len(orders) == 1,
                        type="均衡" if first and second / first >= BALANCED_SECOND_SHARE else "单极", lines=lines)
        elif character in FALLBACK_ORDER:
            text = FALLBACK_ORDER[character]
            idol.update(order=[_SHORT[text[i:i + 2]] for i in (0, 2, 4)], source="table", lines={})
        else:
            continue
        idols.append(idol)
    for produce_id, step, number in seen:
        numbers[(produce_id, step)] = max(numbers[(produce_id, step)], number)
    exams = [{"id": exam_id(p, s, n), "scenario": PRODUCES[p][0], "label": _exam_label(p, s, n, numbers[(p, s)])}
             for p in PRODUCES for s in sorted(STEP) for n in range(1, numbers.get((p, s), 0) + 1) if (p, s, n) in seen]
    return {"schema": SCHEMA, "scenarios": [{"id": k, "label": v, "headline": HEADLINE[k]} for k, v in SCENARIOS],
            "exams": exams, "idols": idols}


def table(master_dir=None):
    """build(), kept until one of the four tables changes on disk."""
    folder = _master_dir(master_dir)
    stamp = tuple((folder / f"{n}.jsonl").stat().st_mtime_ns if (folder / f"{n}.jsonl").is_file() else 0
                  for n in ("IdolCard", "ProduceStepAuditionDifficulty", "ProduceExamBattleConfig", "Character"))
    hit = _CACHE.get(str(folder))
    if hit is None or hit[0] != stamp:
        hit = _CACHE[str(folder)] = (stamp, build(folder))
    return hit[1]


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--master", default=None)
    args = parser.parse_args(argv)
    print(json.dumps(table(args.master), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
