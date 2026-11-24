# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""a memory's skill cards and their cost (占容) -- which cards of a finished produce's deck go into its memory.

    python -B GakumasCLI/gkms.py scenario.memory_cost check FRAMES [FRAMES ...]   every memory in the frames against its grade's box
    python -B GakumasCLI/gkms.py scenario.memory_cost draw GRADE CARD [CARD ...]   the draw distribution of a deck (CARD = p_card-...+1)
    python -B GakumasCLI/gkms.py scenario.memory_cost cost CARD [CARD ...]         each card's cost and the sum
    python -B GakumasCLI/gkms.py scenario.memory_cost check-records HISTORIES.json  ranking records (tools/produce/hif_histories.py decode)
"""
from __future__ import annotations

import functools
import gzip
import itertools
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
MASTER_DIR = HERE.parent / "ro" / "master" / "tables"
MAX_CARDS = 5
COST_PER_EVALUATION = 3

GRADES = {
    6: ("B", 306, 363), 7: ("B+", 306, 423), 8: ("A", 441, 519), 9: ("A+", 441, 594),
    10: ("S", 546, 642), 11: ("S+", 546, 741), 12: ("SS", 651, 804), 13: ("SS+", 651, 858),
    14: ("SSS", 546, 858), 15: ("SSS+", 546, 858), 16: ("S4", 546, None), 17: ("S4+", 552, 858),
    18: ("S5", 546, None),
}
GRADE_OF = {v[0]: k for k, v in GRADES.items()}
SOURCE = "社区观测（wikiwiki 学マス 初心者指南 コスト編）；评级编号由 69 份记忆实测对上"

_EVAL = None


def evaluations(master_dir=None):
    """{(card id, upgrade count): evaluation} from the master's ProduceCard rows (missing = not decoded)."""
    global _EVAL
    if _EVAL is None or master_dir is not None:
        out = {}
        for line in (Path(master_dir or MASTER_DIR) / "ProduceCard.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            d, outer = row.get("decoded") or row, row.get("outer") or {}
            cid = outer.get("Id") or d.get("id")
            up = int(outer.get("UpgradeCount", d.get("upgradeCount") or 0) or 0)
            if d.get("evaluation") is not None:
                out[(cid, up)] = int(d["evaluation"])
        if master_dir is not None:
            return out
        _EVAL = out
    return _EVAL


_FLAGS = None
LEGEND_CAP = 1


def card_flags():
    """{card id: (noDeckDuplication, LEGEND rarity)} from the master's ProduceCard rows."""
    global _FLAGS
    if _FLAGS is None:
        out = {}
        for line in (MASTER_DIR / "ProduceCard.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                d = row.get("decoded") or row
                cid = (row.get("outer") or {}).get("Id") or d.get("id")
                nodup, legend = out.get(cid, (False, False))
                out[cid] = (nodup or bool(d.get("noDeckDuplication")),
                            legend or (d.get("rarity") or {}).get("name") == "PRODUCE_CARD_RARITY_LEGEND")
        _FLAGS = out
    return _FLAGS


_CLASSES = None


def card_classes():
    """{card id: (rarity, category)} from the master's ProduceCard rows: rarity 'N' / 'R' / 'SR' / 'SSR' / 'LEGEND',
    category 'ACTIVE_SKILL' / 'MENTAL_SKILL' / 'TROUBLE' (the plan's score defaults, scenario/produce_plan.card_score)."""
    global _CLASSES
    if _CLASSES is None:
        out = {}
        for line in (MASTER_DIR / "ProduceCard.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                d = row.get("decoded") or row
                cid = (row.get("outer") or {}).get("Id") or d.get("id")
                if cid not in out:
                    rarity = str((d.get("rarity") or {}).get("name") or "").replace("PRODUCE_CARD_RARITY_", "")
                    category = str((d.get("category") or {}).get("name") or "").replace("PRODUCE_CARD_CATEGORY_", "")
                    out[cid] = (rarity, category)
        _CLASSES = out
    return _CLASSES


def deck_errors(target):
    """[] or why a target deck [[card key, count],...] cannot be built: a 重複不可 card (noDeckDuplication, any upgrade or
    customize) more than once, a support event card (-sup-) more than once, more than LEGEND_CAP LEGEND cards.
    """
    flags, per_id, legend, errors = card_flags(), Counter(), 0, []
    for key, n in target:
        cid, _up = split_key(key)
        per_id[cid] += int(n)
    for cid, n in sorted(per_id.items()):
        nodup, is_legend = flags.get(cid, (False, False))
        if cid not in flags:
            errors.append(f"{cid}：主数据里没有这张卡")
        elif nodup and n > 1:
            errors.append(f"{cid}：重複不可，最多 1 张（现在 {n}）")
        elif "-sup-" in cid and n > 1:
            errors.append(f"{cid}：支援事件卡最多 1 张（现在 {n}）")
        legend += n if is_legend else 0
    if legend > LEGEND_CAP:
        errors.append(f"LEGEND 卡最多 {LEGEND_CAP} 张（现在 {legend}）")
    return errors


def split_key(key):
    """'p_card-xx+1' -> ('p_card-xx', 1); a dict {id, upgrade_count} too."""
    if isinstance(key, dict):
        return key.get("id") or key.get("produce_card_id"), int(key.get("upgrade_count") or key.get("current_upgrade_count") or 0)
    cid, _, up = str(key).partition("+")
    return cid, int(up or 0)


def is_unique(card_id):
    return "-ido-" in str(card_id)


def grade_number(grade):
    """6..18, a name ('SSS', 'SS+') or the wire's enum name ('RESULT_GRADE_SSSS_PLUS') -> its number; None if unknown."""
    if isinstance(grade, int) or (isinstance(grade, str) and grade.isdigit()):
        return int(grade) if int(grade) in GRADES else None
    name = str(grade or "").upper().replace("RESULT_GRADE_", "").replace("_PLUS", "+")
    name = name.replace("SSSSS", "S5").replace("SSSS", "S4")
    return GRADE_OF.get(name)


def box(grade):
    """(lower, upper) of a grade (upper None = unknown)."""
    g = grade_number(grade)
    if g is None:
        raise ValueError(f"不认识的评级：{grade}")
    return GRADES[g][1], GRADES[g][2]


def cost(key, evals=None):
    """A card's cost (3 x evaluation), None for a unique card or a card the master does not know."""
    cid, up = split_key(key)
    if is_unique(cid):
        return None
    e = (evals or evaluations()).get((cid, up))
    return None if e is None else COST_PER_EVALUATION * e


def drawable(deck, evals=None):
    """[(key, cost)] of the deck's cards that can go into a memory, and the keys the master does not price."""
    out, unknown = [], []
    for c in deck:
        cid, up = split_key(c)
        if is_unique(cid):
            continue
        k = f"{cid}+{up}"
        v = cost(k, evals)
        (out.append((k, v)) if v is not None else unknown.append(k))
    return out, unknown


def draw(deck, grade, evals=None):
    """{sorted tuple of card keys: probability} of the memory a produce ending with DECK would get at GRADE (rules 1-4;
    ties uniform over the deck's cards).
    """
    lower, upper = box(grade)
    cards, _unknown = drawable(deck, evals)
    best_n, best = -1, Counter()
    below_sum, below = -1, Counter()
    for k in range(min(MAX_CARDS, len(cards)), 0, -1):
        if best_n > k:
            break
        for combo in itertools.combinations(range(len(cards)), k):
            total = sum(cards[i][1] for i in combo)
            if upper is not None and total > upper:
                continue
            key = tuple(sorted(cards[i][0] for i in combo))
            if total >= lower:
                if k > best_n:
                    best_n, best = k, Counter()
                best[key] += 1
            elif best_n < 0 and total >= below_sum:
                if total > below_sum:
                    below_sum, below = total, Counter()
                below[key] += 1
    picked = best if best_n > 0 else below
    n = sum(picked.values())
    return {k: v / n for k, v in picked.items()} if n else {(): 1.0}


def is_support(card_id):
    return "-sup-" in str(card_id)


def _drawn_class(cid):
    """Whether a card can go into a memory: never a basic (N) or a trouble card."""
    rarity, category = card_classes().get(cid, ("", ""))
    return rarity != "N" and category != "TROUBLE"


def kinds(deck, held=(), evals=None):
    """the idol's own cards, basic and trouble cards left out."""
    ids = {split_key(c)[0] for c in deck}
    seen = {}
    for c in list(deck) + list(held or ()):
        cid, up = split_key(c)
        if cid not in ids or is_unique(cid) or not _drawn_class(cid):
            continue
        k = f"{cid}+{up}"
        if k not in seen:
            v = cost(k, evals)
            if v is not None:
                seen[k] = v
    return sorted(seen.items())


def _combos(cards, lower, upper, max_n):
    """Rules 1-4 over CARDS [(key, cost)] for at most MAX_N cards, no card id twice: {sorted keys: probability}."""
    best_n, best = -1, Counter()
    below_sum, below = None, Counter()
    for k in range(min(max_n, len(cards)), 0, -1):
        if best_n > k:
            break
        for combo in itertools.combinations(range(len(cards)), k):
            ids = [split_key(cards[i][0])[0] for i in combo]
            if len(set(ids)) < len(ids):
                continue
            total = sum(cards[i][1] for i in combo)
            if upper is not None and total > upper:
                continue
            key = tuple(sorted(cards[i][0] for i in combo))
            if total >= lower:
                if k > best_n:
                    best_n, best = k, Counter()
                best[key] += 1
            elif best_n < 0 and (below_sum is None or total >= below_sum):
                if below_sum is None or total > below_sum:
                    below_sum, below = total, Counter()
                below[key] += 1
    picked = best if best_n > 0 else below
    n = sum(picked.values())
    return {k: v / n for k, v in picked.items()} if n else {(): 1.0}


def draw_v2(deck, grade, held=(), evals=None):
    """{sorted tuple of card keys: probability} of the memory's non-unique cards under memory model v2."""
    lower, upper = box(grade)
    pool = kinds(deck, held, evals)
    sups = [x for x in pool if is_support(split_key(x[0])[0])]
    rest = [x for x in pool if not is_support(split_key(x[0])[0])]
    if not sups:
        return _combos(rest, lower, upper, MAX_CARDS)
    out = Counter()
    sup_ids = sorted({split_key(k)[0] for k, _ in sups})
    for sid in sup_ids:
        versions = [x for x in sups if split_key(x[0])[0] == sid]
        for k, c in versions:
            sub = _combos(rest, lower - c, None if upper is None else upper - c, MAX_CARDS - 1)
            for combo, p in sub.items():
                out[tuple(sorted((k,) + combo))] += p / len(sup_ids) / len(versions)
    return dict(out)


def p_target(deck, grade, target, evals=None):
    """The chance the memory is exactly TARGET (card keys; order and duplicates as a multiset)."""
    want = tuple(sorted(f"{split_key(t)[0]}+{split_key(t)[1]}" for t in target))
    return draw(deck, grade, evals).get(want, 0.0)


@functools.lru_cache(maxsize=512)
def _p_target_cached(deck, grade, want, held=()):
    got = 0.0
    for combo, p in draw_v2(list(deck), grade, held).items():
        if tuple(sorted(k.split("+")[0] for k in combo)) == want:
            got += p
    return got


def p_target_of(deck, grade, target, held=()):
    """The chance the memory holds exactly TARGET's cards, by card id (which of its held versions a card keeps is
    drawn: see draw_v2), with the default master; HELD: versions the produce held before (draw_v2); cached by (deck,
    grade, target, held): the station asks every 0.4 s."""
    keys = tuple(sorted(f"{split_key(c)[0]}+{split_key(c)[1]}" for c in deck))
    want = tuple(sorted(split_key(t)[0] for t in target))
    return _p_target_cached(keys, str(grade), want, tuple(sorted(set(held or ()))))


def p_contains(deck, grade, wanted, evals=None, held=()):
    """The chance every card of WANTED is in the memory."""
    need = Counter(f"{split_key(t)[0]}+{split_key(t)[1]}" for t in wanted)
    return sum(p for combo, p in draw_v2(deck, grade, held, evals).items() if not need - Counter(combo))


def check_target(target, grade, evals=None):
    """[] or the reasons a memory target cannot be: more than 5 cards, a unique card, one card id twice, an unpriced
    card, over the box."""
    errors = []
    if len(target) > MAX_CARDS:
        errors.append(f"记忆最多 {MAX_CARDS} 张（不算偶像固有卡），现在 {len(target)} 张")
    try:
        lower, upper = box(grade)
    except ValueError as exc:
        return [str(exc)]
    total, ids = 0, set()
    for t in target:
        cid, up = split_key(t)
        if is_unique(cid):
            errors.append(f"{cid}：偶像固有卡不会抽进记忆")
            continue
        if cid in ids:
            errors.append(f"{cid}：同一张卡在记忆里只会出现一次（不论强化）")
        ids.add(cid)
        v = cost(f"{cid}+{up}", evals)
        if v is None:
            errors.append(f"{cid}+{up}：主数据里没有这张卡的占容")
            continue
        total += v
    if upper is not None and total > upper:
        errors.append(f"占容 {total} 超过 {GRADES[grade_number(grade)][0]} 的上限 {upper}（{SOURCE}）")
    return errors


def target_cost(target, evals=None):
    return sum(cost(f"{split_key(t)[0]}+{split_key(t)[1]}", evals) or 0 for t in target)


# -------------------------------------------------------------------------------------------------- check
def memories_in(paths):
    """{user_memory_id: memory dict} of every memory with its exam cards in the frame files (.jsonl / .jsonl.gz)."""
    found = {}
    for path in paths:
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if "exam_battle_produce_cards" not in line:
                    continue
                stack = [json.loads(line)]
                while stack:
                    o = stack.pop()
                    if isinstance(o, dict):
                        if o.get("user_memory_id") and "exam_battle_produce_cards" in o:
                            found[o["user_memory_id"]] = o
                        stack.extend(o.values())
                    elif isinstance(o, list):
                        stack.extend(o)
    return found


def check(paths, evals=None):
    """One row per memory: grade, cards, cost, inside its box or why not (starter memories marked)."""
    rows = []
    for mid, m in sorted(memories_in(paths).items()):
        cards, unknown = drawable(m.get("exam_battle_produce_cards") or [], evals)
        total = sum(c for _, c in cards)
        customized = sum(1 for c in m.get("exam_battle_produce_cards") or () if c.get("customizes"))
        g = m.get("grade")
        row = {"memory": mid[:8], "grade": g, "grade_name": GRADES.get(g, ("?",))[0], "cards": len(cards),
               "cost": total, "customized_cards": customized, "unknown": unknown,
               "starter": bool(m.get("no_produce_history"))}
        if g in GRADES and not row["starter"]:
            lower, upper = GRADES[g][1], GRADES[g][2]
            row["inside"] = (upper is None or total <= upper) and (total >= lower or len(cards) < MAX_CARDS)
        rows.append(row)
    return rows


def check_records(rows, evals=None):
    """One row per ranking record (hif_histories.py decode): the memory against the final deck and its grade's box."""
    out = []
    for r in rows:
        m = r.get("memory")
        if not m:
            continue
        deck = {}
        for pc in r.get("produce_cards") or ():
            c = (pc or {}).get("produce_card") or {}
            deck.setdefault(c.get("id"), []).append(int(c.get("upgrade_count") or 0))
        cards, unknown = drawable(m.get("exam_battle_produce_cards") or [], evals)
        ids = [split_key(k)[0] for k, _ in cards]
        g = grade_number(m.get("grade"))
        total = sum(c for _, c in cards)
        row = {"idol": r.get("idol_card_id"), "grade": m.get("grade"), "cards": len(cards), "cost": total,
               "unknown": unknown, "from_deck": all(i in deck for i in ids),
               "below_deck_upgrade": sum(1 for k, _ in cards if split_key(k)[1] < max(deck.get(split_key(k)[0]) or [0]))}
        if g is not None:
            lower, upper = GRADES[g][1], GRADES[g][2]
            row["inside"] = (upper is None or total <= upper) and (total >= lower or len(cards) < MAX_CARDS)
            final = [f"{c}+{u}" for c, ups in deck.items() for u in ups]
            held = [f"{c}+0" for c, ups in deck.items() if any(ups)]
            row["possible_v2"] = draw_v2(final, g, held, evals).get(tuple(sorted(k for k, _ in cards)), 0.0) > 0
        out.append(row)
    return out


def main(argv):
    if argv and argv[0] == "check-records":
        rows = check_records(json.loads(Path(argv[1]).read_text(encoding="utf-8")))
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        judged = [r for r in rows if "inside" in r and not r["unknown"]]
        print(json.dumps({"records": len(rows), "from_deck": sum(r["from_deck"] for r in rows), "judged": len(judged),
                          "inside": sum(r["inside"] for r in judged),
                          "below_deck_upgrade": sum(1 for r in rows if r["below_deck_upgrade"]),
                          "possible_v2": sum(1 for r in rows if r.get("possible_v2"))}, ensure_ascii=False))
        return 0
    if not argv or argv[0] not in ("check", "draw", "cost"):
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "check":
        rows = check([Path(a) for a in args])
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        judged = [r for r in rows if "inside" in r]
        print(json.dumps({"memories": len(rows), "judged": len(judged), "inside": sum(r["inside"] for r in judged),
                          "starter": sum(r["starter"] for r in rows)}, ensure_ascii=False))
        return 0
    if cmd == "draw":
        dist = draw(args[1:], args[0])
        for combo, p in sorted(dist.items(), key=lambda kv: -kv[1])[:20]:
            print(f"{p:.4f}  {sum(cost(k) or 0 for k in combo)}  {' '.join(combo)}")
        return 0
    for k in args:
        print(k, cost(k))
    print("sum", sum(cost(k) or 0 for k in args))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
