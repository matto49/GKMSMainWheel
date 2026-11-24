# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""控分预设方案 (gkms.split_preset.v1) -- one 控分 and one 审查目标 per H.I.F audition, saved and shared."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import zlib

from GakumasCLI.scenario import timeline as TL

FORMAT = "gkms.split_preset.v1"
SHARE_PREFIX = "GKSP1:"
SHORT_PREFIX = "SP-"
SHORT_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
SHORT_LEN = 8
MAX_BYTES = 16 * 1024
NAME_CHARS = 40
STAGES = ("sel1", "sel2", "sel3", "honsen1", "honsen2")
STAGE_LABELS = {"sel1": "选拔 1", "sel2": "选拔 2", "sel3": "选拔 3", "honsen1": "本战上半场", "honsen2": "本战下半场"}
_FROM_TIMELINE = {"hif.sel1": "sel1", "hif.sel2": "sel2", "hif.sel3": "sel3", "hif.main1": "honsen1",
                  "hif.main2": "honsen2"}


class PresetError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors[:5]))


def stage_of(meta):
    """The preset stage of an audition (its loadout meta: produce_id, step_type), or None (not an H.I.F audition)."""
    return _FROM_TIMELINE.get(TL.stage_key(meta) or "")


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def check(doc):
    """(preset, errors): the document in canonical form (empty cells and stages left out) or every reason it is
    refused, in Chinese."""
    if not isinstance(doc, dict):
        return None, ["预设方案要是一个对象"]
    if doc.get("format") not in (None, FORMAT):
        return None, [f"format 应为 {FORMAT}"]
    errors = []
    name = " ".join(str(doc.get("name") or "").split())[:NAME_CHARS] or "未命名方案"
    stages = doc.get("stages") or {}
    if not isinstance(stages, dict):
        return None, ["stages 要是一个对象"]
    out = {}
    for key, cell in stages.items():
        if key not in STAGES:
            errors.append(f"没有这个阶段：{key}（{' / '.join(STAGES)}）")
            continue
        label = STAGE_LABELS[key]
        if cell in (None, {}):
            continue
        if not isinstance(cell, dict):
            errors.append(f"{label}：格式不对")
            continue
        if cell.get("same_as") is not None:
            if key != "honsen2" or cell.get("same_as") != "honsen1":
                errors.append(f"{label}：只有本战下半场能「同上半场」")
            else:
                out[key] = {"same_as": "honsen1"}
            continue
        split, why = TL.split_check(cell.get("split") or None)
        errors += [f"{label} 控分：{w}" for w in why]
        goal, why = TL.goal_check(cell.get("goal") or None)
        errors += [f"{label} 目标：{w}" for w in why]
        kind = "honsen1" if key == "honsen1" else "honsen2" if key == "honsen2" else key
        if goal is not None and goal["mode"] not in TL.goal_modes(kind):
            errors.append(f"{label} 目标：只能是 {' / '.join(TL.goal_label(kind, m) for m in TL.goal_modes(kind))}")
        row = {}
        if split is not None:
            row["split"] = split
        if goal is not None:
            row["goal"] = goal
        if row:
            out[key] = row
    if errors:
        return None, errors
    preset = {"format": FORMAT, "name": name, "stages": {k: out[k] for k in STAGES if k in out}}
    if len(canonical(preset).encode("utf-8")) > MAX_BYTES:
        return None, ["预设方案太大"]
    return preset, []


def parse(doc):
    """``check`` that raises PresetError."""
    preset, errors = check(doc)
    if errors:
        raise PresetError(errors)
    return preset


def cell(preset, stage):
    """The cell of ``stage`` ({split?, goal?}, 下半场「同上半场」 resolved) or {}."""
    stages = (preset or {}).get("stages") or {}
    row = stages.get(stage) or {}
    if row.get("same_as"):
        row = stages.get(row["same_as"]) or {}
    return copy.deepcopy(row)


def full_code(preset):
    raw = canonical(parse(preset)).encode("utf-8")
    return SHARE_PREFIX + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")


def from_full_code(text):
    text = str(text or "").strip()
    if not text.startswith(SHARE_PREFIX):
        raise PresetError([f"完整码要以 {SHARE_PREFIX} 开头"])
    body = text[len(SHARE_PREFIX):]
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)), bufsize=MAX_BYTES)
        if len(raw) > MAX_BYTES:
            raise ValueError("too large")
        doc = json.loads(raw.decode("utf-8"))
    except Exception as exc:                                    # noqa: BLE001
        raise PresetError([f"完整码无法解开: {type(exc).__name__}"]) from exc
    return parse(doc)


def short_code(preset):
    """SP- + 8 characters of the canonical document's sha256 (the same content, the same code)."""
    n = int(hashlib.sha256(canonical(parse(preset)).encode("utf-8")).hexdigest(), 16)
    chars = []
    for _ in range(SHORT_LEN):
        n, r = divmod(n, len(SHORT_ALPHABET))
        chars.append(SHORT_ALPHABET[r])
    return SHORT_PREFIX + "".join(chars)


def normalize_short(text):
    """'sp-k7q2 m9xa' -> 'SP-K7Q2M9XA', or None when it is not a short code."""
    t = "".join(str(text or "").split()).upper()
    if not t.startswith(SHORT_PREFIX):
        return None
    body = t[len(SHORT_PREFIX):].replace("-", "")
    if len(body) != SHORT_LEN or any(c not in SHORT_ALPHABET for c in body):
        return None
    return SHORT_PREFIX + body


def goal_text(stage, goal):
    if not goal:
        return "最高分" if stage in ("honsen1", "honsen2") else "默认目标"
    kind = stage
    text = TL.goal_label(kind, goal["mode"])
    if goal["mode"] == "custom":
        text = f"自定义 {int(goal['custom']):,}·最省体力"
    if goal.get("margin"):
        text += f" +{goal['margin'] / 10:g}%"
    return text


def cell_text(preset, stage):
    """「Vo ≥60% · 达标·最省体力 +5%」 for one stage; 下半场「同上半场」 says so."""
    raw = ((preset or {}).get("stages") or {}).get(stage) or {}
    row = cell(preset, stage)
    split = TL.split_text(row.get("split")) if row.get("split") else "不控分"
    text = f"{split} · {goal_text(stage, row.get('goal'))}"
    return ("同上半场：" + text) if raw.get("same_as") else text


def summary(preset):
    """One line: the stages that set something."""
    parts = []
    for key in STAGES:
        row = cell(preset, key)
        if row:
            parts.append(f"{STAGE_LABELS[key]} {cell_text(preset, key)}")
    return "；".join(parts) or "全部不控分、默认目标"


def view(preset):
    """What a page lists for one preset: name, codes, one line per stage."""
    preset = parse(preset)
    return {"name": preset["name"], "short": short_code(preset), "full": full_code(preset), "doc": preset,
            "summary": summary(preset),
            "stages": [{"key": k, "label": STAGE_LABELS[k], "text": cell_text(preset, k),
                        "set": bool(cell(preset, k))} for k in STAGES]}


def editor_meta():
    """The editor's choices: per stage its goal modes (labels), the margins."""
    out = []
    for key in STAGES:
        kind = key
        modes = [{"value": m, "label": TL.goal_label(kind, m)} for m in TL.goal_modes(kind)]
        default = "最高分" if key in ("honsen1", "honsen2") else TL.GOAL_LABELS[
            "score" if key == "sel3" else "stamina"] + "（默认）"
        out.append({"key": key, "label": STAGE_LABELS[key], "modes": modes, "default_label": default})
    return {"format": FORMAT, "stages": out, "margins": list(TL.GOAL_MARGINS)}
