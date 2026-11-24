# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chinese card and drink names for the panel."""
from __future__ import annotations

import json
import os
from pathlib import Path

import GakumasCLI.paths as gkms_paths

DEFAULT_DIR = gkms_paths.TRANS_DIR
SUPPLEMENT = gkms_paths.VENDORED_TRANS_DIR.parent / "supplement.json"
STATUS_TEXT = gkms_paths.VENDORED_TRANS_DIR.parent / "status_text.json"
_CACHE = {}
_STATUS = {}


def status_text(path=None):
    """the patch's Chinese names and texts of the exam statuses (``labels``: Label_Exam* -> [name, text]), the P items
    (``items``: id -> [name, text]) and the persistent effects (``enchants``: id -> text), from
    data/i18n/status_text.json (GakumasTranslationData 994258b, its ``source``); {} when the file is missing."""
    key = str(path or STATUS_TEXT)
    if key not in _STATUS:
        try:
            _STATUS[key] = json.loads(Path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _STATUS[key] = {}
    return _STATUS[key]


def supplement(path=None):
    """({card_id: base name}, {drink_id: name}) the patch lacks."""
    try:
        doc = json.loads(Path(path or SUPPLEMENT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}
    return dict(doc.get("cards") or {}), dict(doc.get("drinks") or {})


def trans_dir():
    value = os.environ.get("GKMS_TRANS_DIR")
    return Path(value) if value else (DEFAULT_DIR or gkms_paths.VENDORED_TRANS_DIR)


def _rows(folder, table):
    path = folder / f"{table}.json"
    if not path.is_file():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    return document.get("data") or [] if isinstance(document, dict) else document


class Translator:
    def __init__(self, master, folder=None):
        folder = Path(folder) if folder else trans_dir()
        self.folder = folder
        self.error = None
        self.cards, self.drinks, self.ja = {}, {}, {}
        for row in _rows(folder, "ProduceCard"):
            if row.get("id") and row.get("name"):
                self.cards[(row["id"], int(row.get("upgradeCount") or 0))] = row["name"]
        for row in _rows(folder, "ProduceDrink"):
            if row.get("id") and row.get("name"):
                self.drinks[row["id"]] = row["name"]
        self.supplemented = set()
        if self.cards or self.drinks:
            extra_cards, extra_drinks = supplement()
            for key in (getattr(master, "cards", None) or {}):
                if isinstance(key, tuple) and key[0] in extra_cards:
                    k = (key[0], int(key[1] or 0))
                    if k not in self.cards:
                        self.cards[k] = extra_cards[key[0]] + "+" * k[1]
                        self.supplemented.add(k)
            for drink_id, name in extra_drinks.items():
                if drink_id not in self.drinks:
                    self.drinks[drink_id] = name
                    self.supplemented.add(drink_id)
        for key, row in (getattr(master, "cards", None) or {}).items():
            if isinstance(key, tuple) and isinstance(row, dict) and row.get("name") and key in self.cards:
                self.ja.setdefault(row["name"], self.cards[key])
        for drink_id, row in (getattr(master, "drinks", None) or {}).items():
            if isinstance(row, dict) and row.get("name") and drink_id in self.drinks:
                self.ja.setdefault(row["name"], self.drinks[drink_id])
        self.enabled = bool(self.cards or self.drinks)

    def card(self, card_id, upgrade, fallback):
        return self.cards.get((card_id, int(upgrade or 0))) or self.name(fallback)

    def drink(self, drink_id, fallback):
        return self.drinks.get(drink_id) or self.name(fallback)

    def name(self, japanese):
        """A Japanese card or drink name (upgrade pluses included) in Chinese."""
        if not japanese or not self.enabled:
            return japanese
        if japanese in self.ja:
            return self.ja[japanese]
        base = japanese.rstrip("+")
        if base in self.ja:
            return self.ja[base].rstrip("+") + "+" * (len(japanese) - len(base))
        return japanese

    def text(self, text):
        """``play <name>`` / ``drink <name>`` / a bare name, translated."""
        if not text or not self.enabled:
            return text
        for prefix in ("play ", "drink "):
            if text.startswith(prefix):
                return prefix + self.name(text[len(prefix):])
        return self.name(text)


    def coverage(self, master):
        """how much of the master the patch translates, and what is missing."""
        cards = sorted(k for k in (getattr(master, "cards", None) or {}) if isinstance(k, tuple))
        missing = [k for k in cards if (k[0], int(k[1] or 0)) not in self.cards]
        drinks = sorted(getattr(master, "drinks", None) or {})
        dmissing = [d for d in drinks if d not in self.drinks]
        return {"folder": str(self.folder), "enabled": self.enabled, "error": self.error,
                "cards": len(cards), "cards_translated": len(cards) - len(missing),
                "card_ids_missing": len({k[0] for k in missing}),
                "cards_missing": [f"{k[0]}+{int(k[1] or 0)}" for k in missing][:300],
                "drinks": len(drinks), "drinks_translated": len(drinks) - len(dmissing),
                "drinks_missing": dmissing,
                "supplemented": sorted(f"{k[0]}+{k[1]}" if isinstance(k, tuple) else k
                                       for k in getattr(self, "supplemented", ()))}


NOTICE = {"text": ""}


def translator(master):
    key = (id(master), str(trans_dir()))
    found = _CACHE.get(key)
    if found is None:
        try:
            found = Translator(master)
        except Exception as exc:
            found = Translator(None, folder=Path("/nonexistent"))
            found.error = f"{type(exc).__name__}: {exc}"[:500]
        _CACHE[key] = found
        try:
            cov = found.coverage(master)
            missing = cov["cards"] - cov["cards_translated"]
            if found.error or not found.enabled:
                NOTICE["text"] = "译名未加载（显示日文）" + (f"：{found.error}" if found.error else "")
            elif missing or cov["drinks_missing"]:
                NOTICE["text"] = f"译名缺 {cov['card_ids_missing']} 张卡、{len(cov['drinks_missing'])} 种饮料（显示日文）"
            else:
                NOTICE["text"] = ""
        except Exception:
            pass
    return found


def translate_advice(advice, tr):
    """a link's advice (next step + plan table) with the patch's names."""
    if not advice or tr is None or not tr.enabled:
        return advice
    out = dict(advice)
    if out.get("next") and out["next"].get("text"):
        out["next"] = dict(out["next"], text=tr.text(out["next"]["text"]))
    if out.get("table"):
        out["table"] = [dict(row, actions=[dict(e, text=tr.text(e.get("text"))) for e in row.get("actions") or ()])
                        for row in out["table"]]
    return out


def translate_snapshot(view, tr):
    """battle_panel.snapshot (the idol road) with the patch's names."""
    if not view or tr is None or not tr.enabled:
        return view
    out = dict(view)
    out["hand"] = [dict(c, name=tr.card(c.get("card_id"), c.get("upgrade_count"), c.get("name")),
                        reason=tr.name(c["reason"]) if isinstance(c.get("reason"), str) else c.get("reason"))
                   for c in view.get("hand") or ()]
    out["hold"] = [dict(c, name=tr.card(c.get("card_id"), 0, c.get("name"))) for c in view.get("hold") or ()]
    if isinstance(view.get("advice"), dict):
        out["advice"] = translate_advice(view["advice"], tr)
    return out
